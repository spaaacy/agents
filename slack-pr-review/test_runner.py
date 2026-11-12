import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import runner

USER = 'U0B11TF094H'
CHANNEL = 'C0B2JHDCQ6M'
A = 'https://github.com/envoy-ai/envoyai-backend/pull/2909'
B = 'https://github.com/envoy-ai/envoyai-web/pull/853'
CFG = {'channel_id': CHANNEL, 'user_id': USER, 'owner': 'envoy-ai', 'github_login': 'spaaacy'}


def result(messages, pagination='End of results - No more pages available.'):
    text = '# Search Results\n\n## Messages\n'
    if not messages:
        text += 'No results found.'
    for i, (ts, body, author) in enumerate(messages, 1):
        text += (f'### Result {i} of {len(messages)}\nChannel: #devs (ID: {CHANNEL})\n'
                 f'From: Person (ID: {author}) \nMessage_ts: {ts}\nText: \n{body}\n\n---\n\n')
    return {'content': [{'type': 'text', 'text': json.dumps({'results': text, 'pagination_info': pagination})}]}


class Matching(unittest.TestCase):
    def match(self, text):
        return runner.assigned_prs(text, USER, 'envoy-ai')

    def test_real_multi_reviewer_message(self):
        self.assertEqual(self.match(f'<@UOTHER|Kaushik> <{A}|backend>\n\n<@{USER}|Aakif> <{B}|web>'),
                         [('envoy-ai/envoyai-web', 853)])

    def test_same_paragraph_separate_reviewers(self):
        self.assertEqual(self.match(f'<@UOTHER> {A}\n<@{USER}> {B}'), [('envoy-ai/envoyai-web', 853)])

    def test_group_and_multiple_links(self):
        self.assertEqual(self.match(f'<@UOTHER> <@{USER}> {A}\n{B}'),
                         [('envoy-ai/envoyai-backend', 2909), ('envoy-ai/envoyai-web', 853)])

    def test_link_before_mention(self):
        self.assertEqual(self.match(f'{A} <@{USER}>'), [('envoy-ai/envoyai-backend', 2909)])

    def test_sole_addressee_in_separate_paragraph(self):
        self.assertEqual(self.match(f'hey <@{USER}>\n\nreview please: {A}'), [('envoy-ai/envoyai-backend', 2909)])

    def test_no_mention_other_org_and_forged_host(self):
        for body in [A, f'<@UOTHER> {A}', f'<@{USER}> https://github.com/evil/repo/pull/1',
                     f'<@{USER}> https://github.com.evil/envoy-ai/repo/pull/1',
                     f'<@{USER}> https://github.com/envoy-ai/repo/pull/123abc']:
            self.assertEqual(self.match(body), [])

    def test_urls_are_data_and_display_copy_deduplicates(self):
        self.assertEqual(self.match(f'<@{USER}> <{A}|{A}> $(touch /tmp/nope)'),
                         [('envoy-ai/envoyai-backend', 2909)])


class Collector(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = runner.database(Path(self.temp.name))
        with self.db:
            runner.put_setting(self.db, 'enabled_at', '100')
            runner.put_setting(self.db, 'checkpoint', '100')
        self.slack = AsyncMock()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def test_retries_and_overlapping_scans_do_not_duplicate(self):
        self.slack.search.return_value = result([('101.000001', f'<@{USER}> {A}', 'UOTHER')])
        await runner.scan(CFG, self.db, self.slack)
        await runner.scan(CFG, self.db, self.slack)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    async def test_no_old_requests_or_own_messages(self):
        self.slack.search.return_value = result([
            ('99.000001', f'<@{USER}> {A}', 'UOTHER'), ('101.000001', f'<@{USER}> {B}', USER)])
        await runner.scan(CFG, self.db, self.slack)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    async def test_pagination_failure_keeps_checkpoint_and_queue(self):
        self.slack.search.side_effect = [result([('101.1', f'<@{USER}> {A}', 'UOTHER')],
            'For the next page of results use cursor `page2`'), RuntimeError('offline')]
        with self.assertRaises(RuntimeError):
            await runner.scan(CFG, self.db, self.slack)
        self.assertEqual(runner.setting(self.db, 'checkpoint'), '100')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    async def test_thread_replies_same_shape_and_multiple_pages(self):
        first = result([('101.1', f'<@{USER}> {A}', 'UOTHER')], 'For the next page use cursor `page2`')
        second = result([('102.1', f'<@{USER}> {B}', 'UOTHER')])
        self.slack.search.side_effect = [first, second]
        self.assertEqual(len(await runner.scan(CFG, self.db, self.slack)), 2)

    async def test_preview_has_no_queue_or_checkpoint_side_effects(self):
        self.slack.search.return_value = result([('101.1', f'<@{USER}> {A}', 'UOTHER')])
        self.assertEqual(len(await runner.scan(CFG, self.db, self.slack, preview=True)), 1)
        self.assertEqual(runner.setting(self.db, 'checkpoint'), '100')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    def test_format_changes_and_wrong_channel_fail_closed(self):
        for value in [{'content': []}, {'isError': True},
                      {'content': [{'type': 'text', 'text': json.dumps({'results': 'surprise'})}]},
                      json.loads(json.dumps(result([('101.1', A, USER)])).replace(CHANNEL, 'CWRONG'))]:
            with self.assertRaises(RuntimeError):
                runner.search_page(value, CHANNEL)


class Publishing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = runner.database(self.root)
        with self.db:
            runner.put_setting(self.db, 'mode', 'post')
            runner.put_setting(self.db, 'posting_authorized_at', '1')
            runner.put_setting(self.db, 'enabled_at', '1')
            self.db.execute("INSERT INTO jobs(key,repo,number,head,base,status,directory,updated) VALUES('key','envoy-ai/repo',1,'head','base','drafted',?,1)", (str(self.root),))
        (self.root / 'payload.json').write_text(json.dumps({'event': 'COMMENT', 'commit_id': 'head', 'body': 'Review', 'comments': []}))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def job(self):
        return self.db.execute('SELECT * FROM jobs').fetchone()

    async def test_disabled_publishing_never_calls_github(self):
        with self.db:
            self.db.execute("DELETE FROM settings WHERE key='posting_authorized_at'")
        with patch.object(runner, 'github', new_callable=AsyncMock) as gh:
            await runner.publish(CFG, self.db, self.job())
            gh.assert_not_called()

    async def test_changed_head_or_base_never_posts(self):
        for head, base in [('changed', 'base'), ('head', 'changed')]:
            with patch.object(runner, 'existing_review', new_callable=AsyncMock, return_value=None), \
                 patch.object(runner, 'github', new_callable=AsyncMock, return_value={
                     'state': 'open', 'head': {'sha': head}, 'base': {'sha': base}}) as gh:
                await runner.publish(CFG, self.db, self.job())
                self.assertEqual(gh.await_count, 1)
                self.assertEqual(self.job()['status'], 'superseded')

    async def test_server_completed_previous_post_is_not_reposted(self):
        with patch.object(runner, 'existing_review', new_callable=AsyncMock, return_value='https://github.com/review/1'), \
             patch.object(runner, 'github', new_callable=AsyncMock) as gh:
            await runner.publish(CFG, self.db, self.job())
            gh.assert_not_called()
            self.assertEqual(self.job()['status'], 'posted')

    async def test_disable_during_github_reads_prevents_post(self):
        async def get(endpoint_cfg, endpoint, *args):
            if endpoint == 'user':
                with self.db:
                    self.db.execute("DELETE FROM settings WHERE key='posting_authorized_at'")
                return {'login': 'spaaacy'}
            return {'state': 'open', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}}
        with patch.object(runner, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(runner, 'github', new_callable=AsyncMock, side_effect=get) as gh:
            await runner.publish(CFG, self.db, self.job())
            self.assertEqual(gh.await_count, 2)
            self.assertEqual(self.job()['status'], 'drafted')

    async def test_timeout_after_post_stays_uncertain_and_is_not_retried(self):
        with patch.object(runner, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(runner, 'github', new_callable=AsyncMock, side_effect=[
                 {'state': 'open', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}},
                 {'login': 'spaaacy'}, TimeoutError('lost response')]) as gh:
            with self.assertRaises(TimeoutError):
                await runner.publish(CFG, self.db, self.job())
            self.assertEqual(self.job()['status'], 'uncertain')
            await runner.publish(CFG, self.db, self.job())
            self.assertEqual(gh.await_count, 3)

    def test_diff_anchors_and_comment_only_exact_commit(self):
        diff = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n same\n-old\n+new\n+extra\n'
        draft = {'status': 'ready', 'reason': '', 'body': 'Fix the issue.', 'comments': [
            {'path': 'a.py', 'line': 2, 'body': 'Blocking: new issue'},
            {'path': 'a.py', 'line': 1, 'body': 'Should fix: context'},
            {'path': 'a.py', 'line': 2, 'body': 'Related detail'}]}
        payload = runner.review_payload(draft, diff, 'abc', 'key')
        self.assertEqual(payload['event'], 'COMMENT')
        self.assertEqual(payload['commit_id'], 'abc')
        self.assertEqual(len(payload['comments']), 1)
        self.assertIn('Related detail', payload['comments'][0]['body'])
        self.assertIn('a.py:1', payload['body'])


class Reviewer(unittest.TestCase):
    def test_reviewer_follows_the_shared_engine_switch(self):
        with tempfile.TemporaryDirectory() as root:
            db = runner.database(Path(root))
            choice_file = Path(root) / 'engine.json'
            with patch.object(runner.ENGINE, 'CHOICE_FILE', choice_file):
                self.assertEqual(runner.current_reviewer(CFG, db), 'codex')
                runner.ENGINE.choose('claude')
                self.assertEqual(runner.current_reviewer(CFG, db), 'claude')
                choice_file.write_text('{"engine": "something-else"}')
                self.assertEqual(runner.current_reviewer(CFG, db), 'codex')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            self.assertIn('reviewer', columns)

    def test_claude_runs_read_only_with_the_allow_list_last(self):
        cfg = {**CFG, 'claude': 'claude', 'claude_allowed_tools': ['Read', 'Bash(git log *)']}
        cmd = runner.claude_command(cfg, 'sess', runner.SCHEMA)
        self.assertEqual(cmd[cmd.index('--setting-sources') + 1], '')
        self.assertEqual(cmd[cmd.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(cmd[cmd.index('--resume') + 1], 'sess')
        self.assertEqual(json.loads(cmd[cmd.index('--json-schema') + 1]), runner.SCHEMA)
        self.assertEqual(cmd[cmd.index('--allowedTools'):], ['--allowedTools', 'Read', 'Bash(git log *)'])
        settings = json.loads(runner.CLAUDE_SETTINGS.read_text())
        for denied in ('Edit', 'Write', 'Bash(git push *)', 'Bash(gh api *)', 'Bash(gh pr review *)'):
            self.assertIn(denied, settings['permissions']['deny'])

    def test_claude_result_reads_the_last_result_and_refuses_errors(self):
        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / 'e.jsonl'
            events.write_text('{"type":"system"}\nnot json\n{"type":"result","result":"done","session_id":"s1",'
                              '"structured_output":{"status":"ready"}}\n')
            final = runner.claude_result(events)
            self.assertEqual(final['session_id'], 's1')
            self.assertEqual(final['structured_output'], {'status': 'ready'})
            events.write_text('{"type":"result","is_error":true,"result":"boom"}\n')
            with self.assertRaises(RuntimeError):
                runner.claude_result(events)

    def test_skill_text_drops_frontmatter(self):
        text = runner.skill_text('review')
        self.assertFalse(text.startswith('---'))
        self.assertIn('$ARGUMENTS', text)


if __name__ == '__main__':
    unittest.main()
