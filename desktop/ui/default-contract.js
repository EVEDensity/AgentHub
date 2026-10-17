// Projection of app/services/default_mission_contract.py, guarded by
// tests/contracts/test_default_task_contract.py. No tool permissions are added.
function buildDefaultTaskContract(contractId, timeSeconds = 300) {
  return {
    id: contractId,
    version: 1,
    repositoryScopes: [],
    allowedCapabilities: [],
    budgets: { timeSeconds, modelCost: 1, retries: 0 },
    acceptanceCriteria: [{
      id: 'desktop-artifacts',
      kind: 'manual',
      description: 'Output integrity only: artifacts exist and match registered size and SHA-256. Does not prove code correctness or test execution.',
      required: true,
      configuration: {
        evaluator: 'artifact-set.v1',
        workUnitKinds: ['desktop.task'],
        minimumArtifacts: 1,
        requiredArtifactKinds: ['test-result'],
      },
    }],
    decisionGates: [],
    forbiddenActions: [],
  };
}
