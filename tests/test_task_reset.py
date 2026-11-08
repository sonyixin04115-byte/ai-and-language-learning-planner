"""Per-task reset clears only timing and never another task or user."""
import unittest

import test_task_timing as timing

app=timing.app


class TaskResetTests(unittest.TestCase):
    setUp=timing.TaskTimingTests.setUp
    tearDown=timing.TaskTimingTests.tearDown
    req=timing.TaskTimingTests.req
    create=timing.TaskTimingTests.create
    task=timing.TaskTimingTests.task
    at=timing.TaskTimingTests.at
    overlapping_updates=timing.TaskTimingTests.overlapping_updates

    def raw(self,tid):
        with app.conn() as c:return dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())

    def assert_clock_only_reset(self,tid):
        before=self.raw(tid)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'reset'})[0],200)
        after=self.raw(tid)
        self.assertEqual({k:v for k,v in after.items() if k not in {'actual','started_at','finished_at','pause_history'}},
                         {k:v for k,v in before.items() if k not in {'actual','started_at','finished_at','pause_history'}})
        self.assertEqual((after['actual'],after['started_at'],after['finished_at'],after['pause_history']),(0,None,None,'[]'))
        return after

    def test_reset_running_paused_completed_and_manual_minutes_preserves_content(self):
        ids=[self.create(note='保留笔记',actual=23),
             self.create(note='正在进行',started_at='2026-11-01T01:00:00Z'),
             self.create(note='已经完成',done=True,started_at='2026-11-01T01:00:00Z',finished_at='2026-11-01T01:45:00Z')]
        paused=self.create(note='暂停的任务',started_at='2026-11-01T01:00:00Z')
        with self.at('2026-11-01T01:10:00Z'):self.assertEqual(self.req(f'/tasks/{paused}','PATCH',{'action':'pause'})[0],200)
        ids.append(paused)
        for tid in ids:
            with self.subTest(tid=tid):self.assert_clock_only_reset(tid)
        self.assertEqual(self.task(ids[2])['done'],1)

    def test_legacy_clock_and_unusual_untouched_fields_are_preserved_exactly(self):
        tid=self.create()
        with app.conn() as c:
            c.execute("UPDATE tasks SET title='  原有标题  ',category='  原分类  ',planned=999,note='原笔记\n第二行',done=1,actual=35,started_at='2020-01-01T01:00:00Z',pause_history='broken-old-clock' WHERE id=?",(tid,))
        self.assert_clock_only_reset(tid)
        self.assertEqual((self.raw(tid)['done'],self.raw(tid)['planned']),(1,999))

    def test_reset_is_idempotent_and_new_timing_starts_from_zero(self):
        tid=self.create(started_at='2026-11-01T01:00:00Z',finished_at='2026-11-01T01:45:00Z')
        first=self.assert_clock_only_reset(tid)
        self.assertEqual(self.assert_clock_only_reset(tid),first)
        with self.at('2026-11-08T03:00:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],200)
        self.assertEqual(self.task(tid)['started_at'],'2026-11-08T03:00:00Z')
        with self.at('2026-11-08T03:05:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],200)
        self.assertEqual(self.task(tid)['actual'],5)

    def test_reset_completed_task_keeps_completion_until_user_unchecks(self):
        tid=self.create(done=True,actual=20)
        self.assert_clock_only_reset(tid)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],400)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':False})[0],200)
        with self.at('2026-11-08T03:00:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],200)
        self.assertEqual(self.task(tid)['done'],0)

    def test_other_tasks_and_rest_day_are_untouched_and_clear_survives_init(self):
        cleared=self.create(actual=20,done=True)
        sibling=self.create(actual=31,done=True)
        self.assertEqual(self.req('/days/rest','POST',{'day':'2026-11-08','rest':True})[0],200)
        sibling_before=self.raw(sibling)
        self.assert_clock_only_reset(cleared)
        app.init();app.init()
        self.assertEqual(self.raw(sibling),sibling_before)
        self.assertEqual(self.task(cleared)['actual'],0)
        self.assertEqual(self.req('/state')[1]['rest_days'],['2026-11-08'])

    def test_invalid_or_unauthorized_resets_do_not_modify_saved_rows(self):
        tid=self.create(actual=25,note='不能覆盖')
        before=self.raw(tid)
        for payload in ({'action':'reset','actual':0},{'action':'reset','note':'改动'},{'action':'reset','done':False},
                        {'action':'reset','started_at':None},{'action':'reset','pause_intervals':[]},
                        {'action':None},{'action':False},[]):
            with self.subTest(payload=payload):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',payload)[0],400)
            self.assertEqual(self.raw(tid),before)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'reset'},token=self.other_token)[0],404)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'reset'},token=None)[0],401)
        self.assertEqual(self.req(f'/tasks/{tid}','GET',{'action':'reset'})[0],405)
        self.assertEqual(self.raw(tid),before)
        self.assertEqual(self.req('/tasks','POST',{'title':'invalid reset create','day':'2026-11-08','planned':30,'action':'reset'})[0],400)
        with app.conn() as c:c.execute('UPDATE tasks SET archived=1 WHERE id=?',(tid,))
        archived=self.raw(tid)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'reset'})[0],404)
        self.assertEqual(self.raw(tid),archived)

    def test_overlapping_reset_then_completion_does_not_resurrect_the_clock(self):
        tid=self.create(started_at='2026-11-01T01:00:00Z',note='保留')
        self.overlapping_updates(tid,{'action':'reset'},'2026-11-01T01:20:00Z','2026-11-01T01:21:00Z',second_payload={'done':True})
        saved=self.task(tid)
        self.assertEqual((saved['actual'],saved['started_at'],saved['finished_at'],saved['pause_intervals'],saved['done']),(0,None,None,[],1))
        self.assertEqual(saved['note'],'保留')

    def test_overlapping_completion_then_reset_preserves_completed_state(self):
        tid=self.create(started_at='2026-11-01T01:00:00Z')
        self.overlapping_updates(tid,{'done':True},'2026-11-01T01:20:00Z','2026-11-01T01:21:00Z',second_payload={'action':'reset'})
        saved=self.task(tid)
        self.assertEqual((saved['actual'],saved['started_at'],saved['finished_at'],saved['pause_intervals'],saved['done']),(0,None,None,[],1))


if __name__=='__main__':unittest.main(verbosity=2)
