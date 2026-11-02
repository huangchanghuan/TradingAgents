from tradingagents.agents.state import AgentState


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        debate = state["investment_debate_state"]
        if debate["count"] >= 2 * self.max_debate_rounds:
            # max_debate_rounds turns each for bull and bear
            return "Research Manager"
        # Route by turn parity, not by the response text: the turn prefix is
        # stamped via ``debate_label`` and follows the report language ("Bull
        # Analyst" vs "多方研究员"), so a prefix match silently starved the
        # bear in Chinese runs. The graph always opens with the bull (count=0),
        # so an odd count means the bull has just spoken.
        if debate["count"] % 2 == 1:
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # max_risk_discuss_rounds turns each for the three risk analysts
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
