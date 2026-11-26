"""Fixed baselines and optional upstream TreeQuest AB-MCTS-A, same ask/tell contract.

Use `selection.py`, never `selectors.py` (would shadow Python's standard library).
"""
import json,uuid,sys,importlib.metadata
from harness.util import HarnessError

class Baseline:
    def __init__(self,config):self.name=config['name']
    def new(self):return {'nodes':{},'pending':{},'asked':0}
    def ask(self,state,actions):
        parent=None
        if self.name=='greedy' and state['nodes']:
            parent=max(state['nodes'],key=lambda n:(state['nodes'][n]['reward'],-state['nodes'][n]['order']))
        action=actions[state['asked']%len(actions)];tid=uuid.uuid4().hex
        state['pending'][tid]={'parent':parent,'action':action};state['asked']+=1
        return state,tid,parent,action
    def tell(self,state,tid,node,reward):
        if tid not in state['pending']:raise HarnessError('Selector trial not pending')
        del state['pending'][tid]
        state['nodes'][node['node_id']]={'reward':reward,'order':len(state['nodes'])}
        return state
    def dump(self,state):return json.dumps(state,sort_keys=True).encode()
    def load(self,data):return json.loads(data)

class TreeQuest:
    """Optional upstream library. Checkpoint pickle stays local and hash-validated."""
    def __init__(self,config):
        import numpy as np
        import treequest
        self.np=np;self.module=treequest
        try:version=importlib.metadata.version('treequest')
        except importlib.metadata.PackageNotFoundError:raise HarnessError('treequest not installed') from None
        if version!=config.get('treequest_version'):
            raise HarnessError(f'TreeQuest version mismatch: {version}, expected {config.get("treequest_version")}')
        self.version=version;self.python=sys.version.split()[0];self.seed=config.get('rng_seed',0)
        self.algo=treequest.ABMCTSA(dist_type=config.get('dist_type','beta'))
    def new(self):
        rng=self.np.random.RandomState(self.seed)
        return {'algo':self.algo.init_tree(),'rng':rng.get_state(),'version':self.version,'python':self.python}
    def ask(self,state,actions):
        self.np.random.set_state(state['rng']);state['algo'],trial=self.algo.ask(state['algo'],actions)
        state['rng']=self.np.random.get_state()
        parent=None if trial.node_to_expand==-1 else trial.parent_state['node_id']
        return state,trial.trial_id,parent,trial.action
    def tell(self,state,tid,node,reward):
        if tid not in state['algo'].trial_store.running_trials:raise HarnessError('TreeQuest trial not pending')
        state['algo']=self.algo.tell(state['algo'],tid,({'node_id':node['node_id'],'artifact_id':node['artifact_id']},reward))
        return state
    def dump(self,state):
        import pickle
        return pickle.dumps(state,protocol=pickle.HIGHEST_PROTOCOL)
    def load(self,data):
        import pickle
        state=pickle.loads(data) # read only verified bytes produced locally, never external checkpoint
        if state['version']!=self.version or state['python']!=self.python:
            raise HarnessError('TreeQuest checkpoint runtime changed; do not silently reset')
        return state

def make_selector(config):
    name=config.get('name')
    if name in ('greedy','multi-start'):return Baseline(config)
    if name=='treequest-abmcts-a':
        try:return TreeQuest(config)
        except ImportError as e:raise HarnessError('Optional TreeQuest unavailable; install pinned treequest==0.3.3: '+str(e)) from None
    raise HarnessError('Unknown selector: '+str(name))
