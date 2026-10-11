import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from resource_governor import Governor, MIB


class ResourceGovernorTest(unittest.TestCase):
    def setUp(self):
        self.g = Governor(2)

    def decide(self, idle=90, available=400, usage=200):
        return self.g.decide(idle, available*MIB, 1000*MIB, usage*MIB)

    def test_headroom_removes_static_limits(self):
        self.assertEqual(self.decide(), {'CPUQuota':'','MemoryHigh':'infinity'})

    def test_cpu_pressure_only_does_not_reclaim_memory(self):
        self.assertEqual(self.decide(idle=19), {'CPUQuota':'40%','MemoryHigh':'infinity'})

    def test_low_memory_throttles_and_targets_reclaim(self):
        result=self.decide(available=100,usage=300)
        self.assertEqual(result['CPUQuota'],'40%')
        self.assertGreaterEqual(int(result['MemoryHigh']),180*MIB)
        self.assertLess(int(result['MemoryHigh']),200*MIB)

    def test_recovery_needs_three_stable_samples(self):
        self.decide(idle=19)
        for idle in [24,26,24,26,26]:
            self.assertEqual(self.decide(idle=idle)['CPUQuota'],'40%')
        self.assertEqual(self.decide(idle=26)['CPUQuota'],'')

    def test_either_resource_can_keep_cpu_throttled(self):
        self.decide(idle=10,available=100)
        for _ in range(3): result=self.decide(idle=90,available=210)
        self.assertEqual(result['CPUQuota'],'40%')
        self.assertNotEqual(result['MemoryHigh'],'infinity')
        for _ in range(3): result=self.decide(idle=90,available=400)
        self.assertEqual(result,{'CPUQuota':'','MemoryHigh':'infinity'})

    def test_small_worker_keeps_positive_soft_limit(self):
        self.assertEqual(int(self.decide(available=1,usage=10)['MemoryHigh']),32*MIB)

    def test_exact_threshold_does_not_trigger(self):
        self.assertEqual(self.decide(idle=20,available=200)['CPUQuota'],'')


if __name__ == '__main__': unittest.main()
