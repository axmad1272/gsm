from decimal import Decimal

import pytest

from app.core.errors import DomainError
from app.modules.shipments.quantities import compute
from app.modules.warehouses.models import QuantityMode


def test_mass_from_volume_and_density():
    q = compute(QuantityMode.VOLUME_DENSITY, volume_l="20000", density="0.840")
    assert q.volume_ml == 20_000_000
    assert q.mass_g == 16_800_000  # 16,800 т
    assert q.computed_field == "mass"


def test_density_from_volume_and_mass():
    q = compute(QuantityMode.VOLUME_MASS, volume_l="20000", mass_t="16.8")
    assert q.mass_g == 16_800_000
    assert q.density == Decimal("0.8400")
    assert q.computed_field == "density"


def test_fractional_liters_are_exact():
    q = compute(QuantityMode.VOLUME_DENSITY, volume_l="1002.5", density="0.8405")
    assert q.volume_ml == 1_002_500
    assert q.mass_g == 842_601  # 1 002 500 мл × 0,8405 = 842 601,25 г → 842 601


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"volume_l": "-1", "density": "0.84"}, "must_be_positive"),
        ({"volume_l": "100", "density": "1.5"}, "density_out_of_range"),
        ({"volume_l": "100", "density": "0.84051"}, "too_precise"),
        ({"volume_l": "100.0001", "density": "0.84"}, "too_precise"),
        ({"volume_l": "100"}, "required"),
    ],
)
def test_invalid(kwargs, code):
    with pytest.raises(DomainError) as e:
        compute(QuantityMode.VOLUME_DENSITY, **kwargs)
    assert e.value.code == code
