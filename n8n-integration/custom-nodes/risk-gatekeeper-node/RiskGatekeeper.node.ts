/**
 * RiskGatekeeper.node.ts
 * ───────────────────────
 * Minimal custom n8n node scaffold for the Risk Gatekeeper middleware.
 *
 * This node should be placed BEFORE any risky action node in an n8n workflow.
 * It POSTs the action details to the Risk Gatekeeper backend, and if the
 * verdict is BLOCK, it throws an error to halt workflow execution.
 * WARN verdicts are logged but do not halt execution in this prototype
 * (a more complete implementation would pause and await human approval via
 * the /decide endpoint).
 *
 * Phase 5 will implement the full integration.
 */
import {
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
  NodeOperationError,
} from 'n8n-workflow';

export class RiskGatekeeper implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Risk Gatekeeper',
    name: 'riskGatekeeper',
    icon: 'fa:shield-alt',
    group: ['transform'],
    version: 1,
    description:
      'Evaluates the current workflow action against the Risk Gatekeeper policy engine before allowing it to proceed.',
    defaults: {
      name: 'Risk Gatekeeper',
    },
    inputs: ['main'],
    outputs: ['main'],
    properties: [
      {
        displayName: 'Backend URL',
        name: 'backendUrl',
        type: 'string',
        default: 'http://localhost:8000/api/evaluate',
        description: 'URL of the Risk Gatekeeper /evaluate endpoint.',
      },
      {
        displayName: 'Original Goal',
        name: 'originalGoal',
        type: 'string',
        default: '',
        description: "The user's stated goal for this workflow session (used for intent drift detection).",
      },
      {
        displayName: 'Action Description',
        name: 'actionDescription',
        type: 'string',
        default: '',
        description: 'Human-readable description of the action this node precedes.',
      },
    ],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const backendUrl = this.getNodeParameter('backendUrl', 0) as string;
    const originalGoal = this.getNodeParameter('originalGoal', 0) as string;
    const actionDescription = this.getNodeParameter('actionDescription', 0) as string;

    // Build the event payload
    const eventPayload = {
      source: 'n8n',
      event_type: 'workflow_action',
      payload: {
        node_name: this.getNode().name,
        action_description: actionDescription,
        input_data: items.map((i) => i.json),
      },
      original_goal: originalGoal || null,
    };

    // POST to the Risk Gatekeeper backend
    let verdict = 'ALLOW';
    let suggestedFix = '';
    try {
      const response = await this.helpers.request({
        method: 'POST',
        uri: backendUrl,
        body: eventPayload,
        json: true,
      });
      verdict = response?.decision?.verdict ?? 'ALLOW';
      suggestedFix = response?.decision?.suggested_fix ?? '';
    } catch (error) {
      // If the backend is unreachable, log a warning but allow execution to continue.
      // TODO (Phase 5): make this configurable — fail-open vs fail-closed.
      console.warn('[RiskGatekeeper] Backend unreachable — defaulting to ALLOW.');
    }

    if (verdict === 'BLOCK') {
      throw new NodeOperationError(
        this.getNode(),
        `Risk Gatekeeper BLOCKED this action. ${suggestedFix}`,
      );
    }

    if (verdict === 'WARN') {
      // TODO (Phase 5): pause execution and call POST /decide/{event_id}
      // For now, log the warning and allow execution to continue.
      console.warn(`[RiskGatekeeper] WARN — ${suggestedFix}`);
    }

    return [items];
  }
}
