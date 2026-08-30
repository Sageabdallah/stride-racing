/**
 * Single source of truth for the Claude model used by every analysis
 * endpoint (chat orchestration, ask-stride, stride-analyst, insights).
 *
 * ANTHROPIC_CHAT_MODEL wins when set (it is part of the stride/prod
 * secret), so the model can be changed without a redeploy.
 */
export const CLAUDE_MODEL =
  process.env.ANTHROPIC_CHAT_MODEL?.trim() || "claude-opus-5";
