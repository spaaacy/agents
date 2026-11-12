import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from hub.core import agent
from hub.core.hub import Hub
from hub.core.store import Job
from hub.plugins.pr_review import domain
from hub.plugins.pr_review import plugin as pr_plugin

USER = 'U0B11TF094H'
CHANNEL = 'C0B2JHDCQ6M'
A = 'https://github.com/envoy-ai/envoyai-backend/pull/2909'
B = 'https://github.com/envoy-ai/envoyai-web/pull/853'
CFG = {'channel_id': CHANNEL, 'user_id': USER, 'owner': 'envoy-ai', 'github_login': 'spaaacy', 'gh': 'gh',
       'poll_seconds': 60, 'review_timeout_seconds': 60, 'claude_allowed_tools': ['Read']}
HUB_CFG = {'owner': {'slack_user_id': USER, 'github_login': 'spaaacy'},
           'plugins': {'pr_review': {'class': 'hub.plugins.pr_review.plugin:PrReview'}}}


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
        return domain.assigned_prs(text, USER, 'envoy-ai')

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
        self.db = domain.database(Path(self.temp.name) / 'state.sqlite3')
        with self.db:
            domain.put_setting(self.db, 'enabled_at', '100')
            domain.put_setting(self.db, 'checkpoint', '100')
        self.slack = AsyncMock()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def test_retries_and_overlapping_scans_do_not_duplicate(self):
        self.slack.search.return_value = result([('101.000001', f'<@{USER}> {A}', 'UOTHER')])
        await domain.scan(CFG, self.db, self.slack)
        await domain.scan(CFG, self.db, self.slack)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 1)

    async def test_overlap_reaches_a_day_back_but_never_before_enabled(self):
        with self.db:
            domain.put_setting(self.db, 'enabled_at', '50')
            domain.put_setting(self.db, 'checkpoint', '200000')
        self.slack.search.return_value = result([])
        await domain.scan(CFG, self.db, self.slack)
        self.assertEqual(self.slack.search.await_args.args[0], str(200000.0 - 86400))
        with self.db:
            domain.put_setting(self.db, 'checkpoint', '1000')
        await domain.scan(CFG, self.db, self.slack)
        self.assertEqual(self.slack.search.await_args.args[0], '50.0')

    async def test_no_old_requests_or_own_messages(self):
        self.slack.search.return_value = result([
            ('99.000001', f'<@{USER}> {A}', 'UOTHER'), ('101.000001', f'<@{USER}> {B}', USER)])
        await domain.scan(CFG, self.db, self.slack)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    async def test_pagination_failure_keeps_checkpoint_and_queue(self):
        self.slack.search.side_effect = [result([('101.1', f'<@{USER}> {A}', 'UOTHER')],
            'For the next page of results use cursor `page2`'), RuntimeError('offline')]
        with self.assertRaises(RuntimeError):
            await domain.scan(CFG, self.db, self.slack)
        self.assertEqual(domain.setting(self.db, 'checkpoint'), '100')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    async def test_thread_replies_same_shape_and_multiple_pages(self):
        first = result([('101.1', f'<@{USER}> {A}', 'UOTHER')], 'For the next page use cursor `page2`')
        second = result([('102.1', f'<@{USER}> {B}', 'UOTHER')])
        self.slack.search.side_effect = [first, second]
        self.assertEqual(len(await domain.scan(CFG, self.db, self.slack)), 2)

    async def test_disabled_scans_nothing(self):
        with self.db:
            self.db.execute("DELETE FROM settings WHERE key='enabled_at'")
        self.assertEqual(await domain.scan(CFG, self.db, self.slack), [])
        self.slack.search.assert_not_called()

    async def test_preview_has_no_queue_or_checkpoint_side_effects(self):
        self.slack.search.return_value = result([('101.1', f'<@{USER}> {A}', 'UOTHER')])
        self.assertEqual(len(await domain.scan(CFG, self.db, self.slack, preview=True)), 1)
        self.assertEqual(domain.setting(self.db, 'checkpoint'), '100')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    def test_format_changes_and_wrong_channel_fail_closed(self):
        for value in [{'content': []}, {'isError': True},
                      {'content': [{'type': 'text', 'text': json.dumps({'results': 'surprise'})}]},
                      json.loads(json.dumps(result([('101.1', A, USER)])).replace(CHANNEL, 'CWRONG'))]:
            with self.assertRaises(RuntimeError):
                domain.search_page(value, CHANNEL)


class Publishing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = domain.database(self.root / 'state.sqlite3')
        with self.db:
            domain.put_setting(self.db, 'mode', 'post')
            domain.put_setting(self.db, 'posting_authorized_at', '1')
            domain.put_setting(self.db, 'enabled_at', '1')
            self.db.execute("INSERT INTO jobs(key,repo,number,head,base,status,directory,updated) "
                            "VALUES('key','envoy-ai/repo',1,'head','base','drafted',?,1)", (str(self.root),))
        (self.root / 'payload.json').write_text(json.dumps({'event': 'COMMENT', 'commit_id': 'head', 'body': 'Review', 'comments': []}))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def job(self):
        return self.db.execute('SELECT * FROM jobs').fetchone()

    async def test_disabled_publishing_never_calls_github(self):
        for removed in ('posting_authorized_at', 'enabled_at'):
            with self.db:
                self.db.execute("DELETE FROM settings WHERE key=?", (removed,))
            with patch.object(domain, 'github', new_callable=AsyncMock) as gh:
                await domain.publish(CFG, self.db, self.job())
                gh.assert_not_called()

    async def test_draft_mode_never_posts(self):
        with self.db:
            domain.put_setting(self.db, 'mode', 'draft')
        with patch.object(domain, 'github', new_callable=AsyncMock) as gh:
            await domain.publish(CFG, self.db, self.job())
            gh.assert_not_called()

    async def test_changed_head_or_base_never_posts(self):
        for head, base in [('changed', 'base'), ('head', 'changed')]:
            with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value=None), \
                 patch.object(domain, 'github', new_callable=AsyncMock, return_value={
                     'state': 'open', 'head': {'sha': head}, 'base': {'sha': base}}) as gh:
                await domain.publish(CFG, self.db, self.job())
                self.assertEqual(gh.await_count, 1)
                self.assertEqual(self.job()['status'], 'superseded')

    async def test_closed_pr_is_superseded(self):
        with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(domain, 'github', new_callable=AsyncMock, return_value={
                 'state': 'closed', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}}):
            await domain.publish(CFG, self.db, self.job())
        self.assertEqual(self.job()['status'], 'superseded')

    async def test_server_completed_previous_post_is_not_reposted(self):
        with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value='https://github.com/review/1'), \
             patch.object(domain, 'github', new_callable=AsyncMock) as gh:
            await domain.publish(CFG, self.db, self.job())
            gh.assert_not_called()
            self.assertEqual(self.job()['status'], 'posted')

    async def test_disable_during_github_reads_prevents_post(self):
        async def get(endpoint_cfg, endpoint, *args):
            if endpoint == 'user':
                with self.db:
                    self.db.execute("DELETE FROM settings WHERE key='posting_authorized_at'")
                return {'login': 'spaaacy'}
            return {'state': 'open', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}}
        with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(domain, 'github', new_callable=AsyncMock, side_effect=get) as gh:
            await domain.publish(CFG, self.db, self.job())
            self.assertEqual(gh.await_count, 2)
            self.assertEqual(self.job()['status'], 'drafted')

    async def test_other_github_account_refuses_to_post(self):
        with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(domain, 'github', new_callable=AsyncMock, side_effect=[
                 {'state': 'open', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}}, {'login': 'someone'}]):
            with self.assertRaises(RuntimeError):
                await domain.publish(CFG, self.db, self.job())
        self.assertEqual(self.job()['status'], 'drafted')

    async def test_timeout_after_post_stays_uncertain_and_is_not_retried(self):
        with patch.object(domain, 'existing_review', new_callable=AsyncMock, return_value=None), \
             patch.object(domain, 'github', new_callable=AsyncMock, side_effect=[
                 {'state': 'open', 'head': {'sha': 'head'}, 'base': {'sha': 'base'}},
                 {'login': 'spaaacy'}, TimeoutError('lost response')]) as gh:
            with self.assertRaises(TimeoutError):
                await domain.publish(CFG, self.db, self.job())
            self.assertEqual(self.job()['status'], 'uncertain')
            await domain.publish(CFG, self.db, self.job())
            self.assertEqual(gh.await_count, 3)

    def test_diff_anchors_and_comment_only_exact_commit(self):
        diff = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n same\n-old\n+new\n+extra\n'
        draft = {'status': 'ready', 'reason': '', 'body': 'Fix the issue.', 'comments': [
            {'path': 'a.py', 'line': 2, 'body': 'Blocking: new issue'},
            {'path': 'a.py', 'line': 1, 'body': 'Should fix: context'},
            {'path': 'a.py', 'line': 2, 'body': 'Related detail'}]}
        payload = domain.review_payload(draft, diff, 'abc', 'key')
        self.assertEqual(payload['event'], 'COMMENT')
        self.assertEqual(payload['commit_id'], 'abc')
        self.assertEqual(len(payload['comments']), 1)
        self.assertIn('Related detail', payload['comments'][0]['body'])
        self.assertIn('a.py:1', payload['body'])
        self.assertIn('<!-- slack-pr-review:key -->', payload['body'])

    def test_blocked_draft_is_never_a_payload(self):
        with self.assertRaises(RuntimeError):
            domain.review_payload({'status': 'blocked', 'reason': 'no tests', 'body': 'x', 'comments': []}, '', 'h', 'k')


class FakeContext:
    def __init__(self, run_dir, results):
        self.run_dir = run_dir
        self.cancel = __import__('threading').Event()
        self.results = list(results)
        self.tasks = []
        self.notes = []

    def run_agent(self, task):
        self.tasks.append(task)
        return self.results.pop(0)

    def note(self, text):
        self.notes.append(text)


PR_JSON = {'state': 'open', 'html_url': A, 'number': 2909, 'head': {'sha': 'h' * 40}, 'base': {'sha': 'b' * 40, 'ref': 'development',
           'repo': {'full_name': 'envoy-ai/envoyai-backend', 'ssh_url': 'git@x'}}}
DRAFT = {'status': 'ready', 'reason': '', 'body': 'Looks good, two nits.\nMore.', 'comments': []}


class Handle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        old = patch.object(pr_plugin, 'OLD_DB', self.root / 'no-old.sqlite3')
        old.start()
        self.addCleanup(old.stop)
        self.hub = Hub(HUB_CFG, state_dir=self.root / 'hub', with_slack=False)
        self.plugin = self.hub.plugins['pr_review']
        db = self.plugin.db()
        with db:
            domain.put_setting(db, 'enabled_at', '1')
            domain.put_setting(db, 'mode', 'draft')
            db.execute("INSERT INTO requests(message_ts,repo,number) VALUES ('101.1','envoy-ai/envoyai-backend',2909)")
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def job(self, attempt=1):
        db = self.plugin.db()
        try:
            queued = self.plugin.enqueue_queued(db)
        finally:
            db.close()
        job = queued[0] if queued else self.hub.store.jobs(plugin='pr_review')[0]
        job.attempt = attempt
        return job

    def run_handle(self, job, results, publish=None):
        ctx = FakeContext(self.root / 'run', results)

        async def fake_prepare(cfg, pr, directory):
            (directory / 'checkout').mkdir(parents=True, exist_ok=True)
            return directory / 'checkout'

        async def fake_finish(pr, checkout, directory, key):
            return domain.review_payload(json.loads((directory / 'draft.json').read_text()), '', pr['head']['sha'], key)

        with patch.object(domain, 'github', new_callable=AsyncMock, return_value=PR_JSON), \
             patch.object(domain, 'prepare', side_effect=fake_prepare), \
             patch.object(domain, 'finish_review', side_effect=fake_finish), \
             patch.object(domain, 'publish', new_callable=AsyncMock, side_effect=publish) as pub, \
             patch.object(pr_plugin.engine, 'engine', return_value='codex'):
            outcome = asyncio.run(self.plugin._handle(job, ctx))
        return outcome, ctx, pub

    def domain_rows(self):
        db = self.plugin.db()
        try:
            return ([dict(r) for r in db.execute('SELECT * FROM jobs')], [dict(r) for r in db.execute('SELECT * FROM requests')])
        finally:
            db.close()

    def test_queued_requests_become_one_hub_job_each(self):
        db = self.plugin.db()
        try:
            self.assertEqual(len(self.plugin.enqueue_queued(db)), 1)
            self.assertEqual(self.plugin.enqueue_queued(db), [])
        finally:
            db.close()
        job = self.hub.store.jobs(plugin='pr_review')[0]
        self.assertEqual((job.title, job.priority), ('envoy-ai/envoyai-backend#2909', 30))

    def test_two_turns_resume_the_first_session_with_the_schema_on_one_engine(self):
        first = agent.AgentResult('codex', text='the review', session='codex:s1', tokens=10)
        second = agent.AgentResult('codex', text=json.dumps(DRAFT), session='codex:s1', structured=DRAFT, tokens=5)
        outcome, ctx, pub = self.run_handle(self.job(), [first, second])
        self.assertEqual(outcome.status, 'done', outcome.error)
        self.assertEqual(outcome.summary, 'drafted: Looks good, two nits.')
        self.assertEqual((outcome.link, outcome.tokens, outcome.engine), (A, 15, 'codex'))
        review, draft = ctx.tasks
        self.assertEqual((review.mode, review.engine, review.session, review.schema), ('review', 'codex', None, None))
        self.assertEqual((draft.engine, draft.session, draft.schema), ('codex', 'codex:s1', domain.SCHEMA))
        self.assertIn('Run $review', review.prompt)
        pub.assert_awaited_once()
        self.assertTrue(outcome.extra['resume_command'].endswith('/checkout && codex resume s1'))
        self.assertEqual(outcome.session, 'codex:s1')
        jobs, requests = self.domain_rows()
        self.assertEqual((jobs[0]['status'], jobs[0]['reviewer'], requests[0]['status']), ('drafted', 'codex', 'done'))

    def test_claude_gets_the_skills_inline_and_its_allow_list(self):
        first = agent.AgentResult('claude', text='the review', session='claude:s1')
        second = agent.AgentResult('claude', session='claude:s1', structured=DRAFT)
        with patch.object(pr_plugin.engine, 'engine', return_value='claude'):
            ctx = FakeContext(self.root / 'run', [first, second])
            with patch.object(domain, 'github', new_callable=AsyncMock, return_value=PR_JSON), \
                 patch.object(domain, 'prepare', new_callable=AsyncMock, return_value=self.root), \
                 patch.object(domain, 'finish_review', new_callable=AsyncMock), \
                 patch.object(domain, 'publish', new_callable=AsyncMock):
                outcome = asyncio.run(self.plugin._handle(self.job(), ctx))
        self.assertEqual(outcome.status, 'done', outcome.error)
        review, draft = ctx.tasks
        self.assertIn('<skill name="review">', review.prompt)
        self.assertIn('<skill name="post-review">', draft.prompt)
        self.assertEqual(review.claude_settings, pr_plugin.CLAUDE_SETTINGS)
        self.assertTrue(review.claude_allowed_tools)
        settings = json.loads(pr_plugin.CLAUDE_SETTINGS.read_text())
        for denied in ('Edit', 'Write', 'Bash(git push *)', 'Bash(gh api *)', 'Bash(gh pr review *)'):
            self.assertIn(denied, settings['permissions']['deny'])

    def test_disabled_reviews_are_cancelled_without_an_agent(self):
        job = self.job()
        db = self.plugin.db()
        domain.disable(db)
        db.close()
        outcome, ctx, pub = self.run_handle(job, [])
        self.assertEqual(outcome.status, 'cancelled')
        self.assertEqual(ctx.tasks, [])
        self.assertEqual(self.domain_rows()[1][0]['status'], 'skipped_disabled')

    def test_a_head_already_reviewed_is_not_reviewed_again(self):
        first = agent.AgentResult('codex', text='r', session='codex:s1')
        second = agent.AgentResult('codex', session='codex:s1', structured=DRAFT)
        self.run_handle(self.job(), [first, second])
        db = self.plugin.db()
        with db:
            db.execute("INSERT INTO requests(message_ts,repo,number) VALUES ('102.1','envoy-ai/envoyai-backend',2909)")
        db.close()
        outcome, ctx, _ = self.run_handle(self.job(), [])
        self.assertEqual(outcome.status, 'done')
        self.assertIn('already reviewed', outcome.summary)
        self.assertEqual(ctx.tasks, [])

    def test_an_unconfirmed_post_is_never_retried(self):
        key = domain.job_key('envoy-ai/envoyai-backend', 2909, 'h' * 40, 'b' * 40)
        db = self.plugin.db()
        with db:
            db.execute("INSERT INTO jobs(key,repo,number,head,base,status,directory,updated) "
                       "VALUES(?,?,?,?,?,'uncertain','/tmp/x',1)", (key, 'envoy-ai/envoyai-backend', 2909, 'h' * 40, 'b' * 40))
        db.close()
        outcome, ctx, _ = self.run_handle(self.job(attempt=2), [])
        self.assertEqual(outcome.status, 'failed')
        self.assertIn('never confirmed', outcome.error)
        self.assertEqual(ctx.tasks, [])

    def test_agent_failure_fails_the_review_and_a_retry_reviews_it_cleanly(self):
        job = self.job()
        outcome, _, _ = self.run_handle(job, [agent.AgentResult('codex', error='boom')])
        self.assertEqual(outcome.status, 'failed')
        jobs, requests = self.domain_rows()
        self.assertEqual((jobs[0]['status'], requests[0]['status']), ('failed', 'failed'))
        failed_dir = Path(jobs[0]['directory'])
        job.attempt = 2
        first = agent.AgentResult('codex', text='r', session='codex:s2')
        second = agent.AgentResult('codex', session='codex:s2', structured=DRAFT)
        outcome, ctx, _ = self.run_handle(job, [first, second])
        self.assertEqual(outcome.status, 'done', outcome.error)
        self.assertEqual(len(ctx.tasks), 2)
        self.assertTrue(any(p.name.startswith(failed_dir.name + '.failed-') for p in failed_dir.parent.iterdir()))

    def test_closed_pr_is_not_reviewed(self):
        with patch.object(domain, 'github', new_callable=AsyncMock, return_value={**PR_JSON, 'state': 'closed'}):
            outcome = asyncio.run(self.plugin._handle(self.job(), FakeContext(self.root, [])))
        self.assertEqual((outcome.status, self.domain_rows()[1][0]['status']), ('done', 'closed'))

    def test_interrupted_reviews_are_failed_on_start(self):
        db = self.plugin.db()
        with db:
            db.execute("UPDATE requests SET status='running'")
            db.execute("INSERT INTO jobs(key,repo,number,head,base,status,directory,updated) "
                       "VALUES('k','r',1,'h','b','reviewing','/tmp/x',1)")
        db.close()
        with patch.object(self.plugin, 'spawn'):
            self.plugin.start()
        jobs, requests = self.domain_rows()
        self.assertEqual((jobs[0]['status'], requests[0]['status']), ('failed', 'failed'))

    def test_slack_commands_cannot_authorize_posting(self):
        commands = self.plugin.commands()
        self.assertIn('terminal-only', commands['enable'](['post']))
        self.assertIn('draft mode', commands['enable'](['draft']))
        self.assertFalse(self.plugin.status()['posting_authorized'])
        self.assertIn('off', commands['disable']([]))
        self.assertFalse(self.plugin.status()['enabled'])


class Migration(unittest.TestCase):
    def test_old_queue_is_copied_once_without_the_old_reviewer_choice(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old = domain.database(root / 'old.sqlite3')
            with old:
                domain.put_setting(old, 'checkpoint', '123')
                domain.put_setting(old, 'reviewer', 'claude')
                old.execute("INSERT INTO requests(message_ts,repo,number,status) VALUES ('1.1','r',1,'done')")
            with patch.object(pr_plugin, 'OLD_DB', root / 'old.sqlite3'):
                hub = Hub(HUB_CFG, state_dir=root / 'hub', with_slack=False)
                db = hub.plugins['pr_review'].db()
                self.assertEqual(domain.setting(db, 'checkpoint'), '123')
                self.assertIsNone(domain.setting(db, 'reviewer'))
                self.assertEqual(db.execute('SELECT status FROM requests').fetchone()[0], 'done')
                with db:
                    domain.put_setting(db, 'checkpoint', '456')
                db.close()
                db = hub.plugins['pr_review'].db()
                self.assertEqual(domain.setting(db, 'checkpoint'), '456')  # copied once, never again
                db.close()
            old.close()

    def test_first_hub_start_copies_the_queue_again_then_never(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old = domain.database(root / 'old.sqlite3')
            with old:
                domain.put_setting(old, 'checkpoint', '1')
            with patch.object(pr_plugin, 'OLD_DB', root / 'old.sqlite3'):
                hub = Hub(HUB_CFG, state_dir=root / 'hub', with_slack=False)
                plugin = hub.plugins['pr_review']
                plugin.status()  # an offline read before cutover copies the queue as it was
                with old:
                    domain.put_setting(old, 'checkpoint', '2')  # the old runner kept going
                with patch.object(plugin, 'spawn'):
                    plugin.start()
                db = plugin.db()
                self.assertEqual(domain.setting(db, 'checkpoint'), '2')
                with db:
                    domain.put_setting(db, 'checkpoint', '3')
                db.close()
                with patch.object(plugin, 'spawn'):
                    plugin.start()
                db = plugin.db()
                self.assertEqual(domain.setting(db, 'checkpoint'), '3')
                db.close()
            old.close()

    def test_enable_post_needs_authorization(self):
        with tempfile.TemporaryDirectory() as root:
            db = domain.database(Path(root) / 's.sqlite3')
            with self.assertRaises(ValueError):
                domain.enable(db, 'post', False)
            with db:
                db.execute("INSERT INTO requests(message_ts,repo,number) VALUES ('1.1','r',1)")
            domain.enable(db, 'post', True)
            self.assertEqual(db.execute('SELECT status FROM requests').fetchone()[0], 'skipped_before_activation')
            self.assertTrue(domain.setting(db, 'posting_authorized_at'))
            db.close()

    def test_skill_text_drops_frontmatter(self):
        text = domain.skill_text('review')
        self.assertFalse(text.startswith('---'))
        self.assertIn('$ARGUMENTS', text)


if __name__ == '__main__':
    unittest.main()
