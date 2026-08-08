from flow_affiliate_ai.providers.flow.base import FlowGenerationRequest
from flow_affiliate_ai.providers.flow.gflow_cli import GFlowCliProvider


def test_credit_estimate_for_ten_second_clip(tmp_path):
    provider = GFlowCliProvider(state_dir=tmp_path)
    request = FlowGenerationRequest(
        job_id="demo",
        prompt="The woman slowly turns toward the camera.",
        duration_seconds=10,
        max_credit_cost=15,
        idempotency_key="demo-key",
    )
    quote = provider.estimate(request)
    assert quote.quoted_credit_cost == 15
    assert quote.can_proceed is True


def test_reference_capability_matches_r2v_mode(tmp_path):
    provider = GFlowCliProvider(state_dir=tmp_path)
    capabilities = provider.capabilities()
    assert capabilities.supports_reference_images is True
    assert capabilities.max_reference_images == 7
