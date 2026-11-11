"""Page processing must not retain cyclic buffers until a late GC sweep."""
import gc
import unittest
import weakref
from unittest.mock import patch
import app


class BatchMemoryTests(unittest.TestCase):
    def test_page_cycles_released_before_next_page(self):
        class Buffer: pass
        refs=[]
        def process(job,index,options):
            self.assertTrue(all(ref() is None for ref in refs))
            value=Buffer();value.cycle=value;value.data=bytearray(1024*1024)
            refs.append(weakref.ref(value))
            return dict(page=index)
        enabled=gc.isenabled();gc.disable()
        try:
            job=dict(count=5,cancel=False,results=[])
            with patch.object(app,'process_page',side_effect=process):app.batch(job,{})
            self.assertEqual(job['state'],'review')
            self.assertEqual(len(job['results']),5)
            self.assertTrue(all(ref() is None for ref in refs))
        finally:
            if enabled:gc.enable()
            gc.collect()

    def test_failed_page_cycles_released(self):
        class Buffer: pass
        refs=[]
        def process(*args):
            value=Buffer();value.cycle=value
            refs.append(weakref.ref(value))
            raise MemoryError()
        job=dict(count=1,cancel=False,results=[])
        with patch.object(app,'process_page',side_effect=process):app.batch(job,{})
        gc.collect()
        self.assertEqual(job['state'],'error')
        self.assertTrue(all(ref() is None for ref in refs))
