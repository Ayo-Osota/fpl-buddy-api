"""
Named strategy weight vectors - squad-optimization's "Strategies Are Named
Weight Vectors". Hardcoded in code (not the database, not accepted from
API input) per suggest-best-squad's design.md Decision 4.

Each strategy is an objective coefficient over exactly the seven
ablatable scoring factors from fpl_data.scoring.performance.
PlayerScoreComponents (availability/discipline are multipliers, not
weighted terms - see player-performance-scoring's existing "Player
Availability from Status and Fitness" - and has_history/next_gw_score are
metadata, not objective terms), plus a fixture horizon and an optional
set of hard constraints the ILP builder understands.
"""

from dataclasses import asdict, dataclass, field

# Objective term names - must match fpl_data.scoring.performance.
# PlayerScoreComponents field names exactly, since fpl_data.optimization
# reads a strategy's weights by these keys. Also the set of factors
# squad-backtesting's ablation can zero out one at a time - see "Every
# weighted factor can be ablated".
OBJECTIVE_TERMS = (
    "expected_component",
    "realized_component",
    "regression_signal",
    "fixture_component",
    "setpiece_component",
    "ownership_component",
    "rotation_component",
)


@dataclass(frozen=True)
class HardConstraint:
    """A strategy-specific constraint beyond the universal FPL rules (see
    squad-optimization's "Squad Selection Satisfies FPL Constraints by
    Construction" for those). Currently one type is supported:
    'min_count_above_price', requiring at least `count` selected players
    priced strictly above `price_tenths` (FPL's native tenths-of-a-million
    unit)."""

    type: str
    price_tenths: int
    count: int


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    horizon: int
    weights: dict
    hard_constraints: tuple = field(default_factory=tuple)

    def weight(self, term):
        return self.weights.get(term, 0.0)


STRATEGIES = {
    "balanced": StrategyConfig(
        name="balanced",
        horizon=5,
        weights={
            "expected_component": 1.0,
            "realized_component": 0.5,
            "regression_signal": 0.5,
            "fixture_component": 1.0,
            "setpiece_component": 1.0,
            "ownership_component": 0.0,
            "rotation_component": 1.0,
        },
    ),
    "premium_heavy": StrategyConfig(
        name="premium_heavy",
        horizon=5,
        weights={
            "expected_component": 1.5,
            "realized_component": 1.0,
            "regression_signal": 0.5,
            "fixture_component": 0.75,
            "setpiece_component": 1.0,
            "ownership_component": 0.1,
            "rotation_component": 0.75,
        },
        hard_constraints=(
            HardConstraint(type="min_count_above_price", price_tenths=110, count=2),
        ),
    ),
    "differential": StrategyConfig(
        name="differential",
        horizon=5,
        weights={
            "expected_component": 1.0,
            "realized_component": 0.5,
            "regression_signal": 1.0,
            "fixture_component": 1.0,
            "setpiece_component": 1.0,
            "ownership_component": -0.15,
            "rotation_component": 0.75,
        },
    ),
    "set_and_forget": StrategyConfig(
        name="set_and_forget",
        horizon=8,
        weights={
            "expected_component": 1.0,
            "realized_component": 0.25,
            "regression_signal": 0.25,
            "fixture_component": 1.5,
            "setpiece_component": 0.5,
            "ownership_component": 0.0,
            "rotation_component": 1.5,
        },
    ),
}


def strategy_to_dict(strategy: StrategyConfig):
    """JSON-serializable snapshot of a strategy's config, for
    ScoringRun.weights - see "Scoring Runs Are Persisted with Component
    Scores": a run stays reproducible/inspectable even if the named
    strategy's coefficients change in code later."""
    return {
        "name": strategy.name,
        "horizon": strategy.horizon,
        "weights": dict(strategy.weights),
        "hard_constraints": [asdict(hc) for hc in strategy.hard_constraints],
    }


def get_strategy(name):
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {name!r}. Configured strategies: {sorted(STRATEGIES)}"
        )
