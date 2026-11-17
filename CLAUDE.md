## Agent layer rules (FreedomBot assistant)

- Permissions are enforced in code (freedombot/rbac/registry.py + the guard), never by prompt instructions.
- Every tool call goes through the guard hook. No tool may bypass it.
- The reply to the user is built from tool results only. Never report an action as done unless
  its tool returned success in this run.
- The existing policy check (freedombot/tools/base.py guarded_call → freedombot/policy/engine.py) is the only
  permission and gating system. New checks may only add strictness; no tool, for any role, may become
  less strict than today.
- Tool descriptions follow docs/agent/TOOL_DESCRIPTIONS.md. Any new or changed tool needs:
  routing domain, description, id_fields, requires_prior, a ToolSpec in freedombot/rbac/registry.py,
  and at least 3 eval cases in evals/cases.yaml whose expected gates come from the policy check.
- Never merge a destructive tool (delete, send, deactivate, reset, sign-off) with another tool.
- Guard, risk tiers, registry metadata and the reply composer are plain Python modules.
  Agno code only wires them together.
- Run the eval suite after any change to prompts, tool descriptions, bundles or the router.
  Never lower pass thresholds or edit expected results to make evals pass.
- Pin the Agno version. Check Agno API names against the installed version.
- Evals and tests use mocked tools only. Never call real email, CRM, calendar or finance systems from tests.