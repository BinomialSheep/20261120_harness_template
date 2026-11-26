"""AHC thin harness: one orchestrator, independent workers, LLM-free evaluation."""
import sys

__version__ = '0.2.0'

if sys.version_info < (3, 10):
    raise SystemExit('ahc thin harness には Python 3.10 以上が必要です（いまは %d.%d）' % sys.version_info[:2])
