import pytest

from app.keitaro import KeitaroError
from app.services.creator import create_campaign, normalize_name
from app.services.errors import NotFoundError, UserError


def create(db, kt, settings, **overrides):
    data = {"name": "campaign1", "countries": "mx, AU", "offer_id": 3749} | overrides
    return create_campaign(db, kt, settings, **data)


def test_creates_campaign_with_two_streams(db, kt, settings):
    campaign = create(db, kt, settings)

    kt_campaign = kt.campaigns[campaign.kt_id]
    assert kt_campaign.name == "test_ivanov_campaign1"
    assert (kt_campaign.domain_id, kt_campaign.group_id, kt_campaign.traffic_source_id) == (1, 7, 9)
    assert kt_campaign.type == "position"

    geo, offers = kt.get_campaign_streams(campaign.kt_id)
    assert geo.position < offers.position  # сначала проверяется гео
    assert geo.schema_ == "redirect" and geo.action_payload == "https://google.com"
    assert [(f.name, f.mode, f.payload) for f in geo.filters] == [
        ("country", "accept", ["MX", "AU"])
    ]
    assert offers.filters == []  # ловит всех остальных
    assert [(o.offer_id, o.share) for o in offers.offers] == [(3749, 100)]

    # кампания сразу сохранена у нас и готова к редактированию
    assert campaign.url == f"https://kylovoa.com/{kt_campaign.alias}"
    assert [s.has_offers for s in campaign.streams] == [False, True]


def test_prefix_is_not_duplicated():
    assert normalize_name("test_ivanov_x", "test_ivanov_") == "test_ivanov_x"
    assert normalize_name("  x ", "test_ivanov_") == "test_ivanov_x"
    with pytest.raises(UserError):
        normalize_name("test_ivanov_", "test_ivanov_")


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"countries": "XX"}, UserError),
        ({"countries": ""}, UserError),
        ({"name": "  "}, UserError),
        ({"offer_id": 424242}, NotFoundError),
    ],
)
def test_invalid_input_creates_nothing(db, kt, settings, overrides, error):
    with pytest.raises(error):
        create(db, kt, settings, **overrides)
    assert "create_campaign" not in kt.calls


def test_missing_group_is_reported(db, kt, settings):
    kt.groups = []
    with pytest.raises(UserError, match="группа"):
        create(db, kt, settings)
    assert "create_campaign" not in kt.calls


def test_failed_stream_rolls_back_campaign(db, kt, settings):
    kt.fail_on["create_stream"] = KeitaroError("boom", 500)
    with pytest.raises(UserError, match="удалена"):
        create(db, kt, settings)
    assert kt.list_campaigns() == []


def test_failed_rollback_asks_to_delete_manually(db, kt, settings):
    kt.fail_on["create_stream"] = KeitaroError("boom", 500)
    kt.fail_on["delete_campaign"] = KeitaroError("also boom", 500)
    with pytest.raises(UserError, match="вручную"):
        create(db, kt, settings)
