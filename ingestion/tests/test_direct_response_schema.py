from ingestion.direct_response_schema import OfferTier, best_offer


class TestBestOffer:
    def test_prefers_flagged_best_value(self) -> None:
        offers = [
            OfferTier(tier_label="1", quantity=1, total_price=39.95, currency="USD"),
            OfferTier(
                tier_label="3", quantity=3, total_price=89.85, currency="USD", is_best_value=True
            ),
        ]
        assert best_offer(offers).quantity == 3

    def test_falls_back_to_single_unit(self) -> None:
        offers = [
            OfferTier(
                tier_label="3", quantity=3, total_price=89.85, price_per_unit=29.95, currency="USD"
            ),
            OfferTier(
                tier_label="1", quantity=1, total_price=39.95, price_per_unit=39.95, currency="USD"
            ),
        ]
        assert best_offer(offers).quantity == 1

    def test_falls_back_to_cheapest_per_unit_when_no_single_unit(self) -> None:
        offers = [
            OfferTier(
                tier_label="6", quantity=6, total_price=180.0, price_per_unit=30.0, currency="USD"
            ),
            OfferTier(
                tier_label="3", quantity=3, total_price=80.0, price_per_unit=26.67, currency="USD"
            ),
        ]
        assert best_offer(offers).quantity == 3

    def test_empty_list_returns_none(self) -> None:
        assert best_offer([]) is None
