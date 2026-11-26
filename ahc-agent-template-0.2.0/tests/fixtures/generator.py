"""Synthetic selection inputs. This is not an AtCoder problem or official tool."""
import random,sys
seed=int(sys.argv[1]);r=random.Random(seed)
print(12,3,seed)
print(*r.sample(range(1,1000),12))
