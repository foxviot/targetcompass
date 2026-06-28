# Agent Contract

All agents in this repository are fixed processing modules, not free-form personas.

Each agent must declare:

- role
- input contract
- output contract
- write ownership
- forbidden actions
- validation gates
- failure behavior
- provenance rules
- claim ceiling

Agent boundaries are strict:

- downstream agents may extend structure, not rewrite upstream facts
- uncertainty must be preserved
- blocked or missing evidence must remain visible
- unsupported causal or therapeutic upgrades are forbidden
