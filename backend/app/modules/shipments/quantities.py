"""Количество топлива. Хранение — целыми: объём в мл, масса в граммах.
Плотность — точная десятичная (кг/л) с 4 знаками.

Ключевое тождество: мл × кг/л = г, поэтому масса считается без промежуточных округлений.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.errors import DomainError
from app.modules.warehouses.models import QuantityMode

DENSITY_MIN = Decimal("0.5000")
DENSITY_MAX = Decimal("1.1000")
DENSITY_STEP = Decimal("0.0001")


@dataclass(frozen=True)
class Quantity:
    volume_ml: int
    mass_g: int
    density: Decimal  # кг/л
    mode: str  # какое значение ввёл оператор, а какое рассчитано

    @property
    def computed_field(self) -> str:
        return "mass" if self.mode == QuantityMode.VOLUME_DENSITY else "density"

    def as_dict(self) -> dict:
        return {
            "volume_ml": self.volume_ml,
            "mass_g": self.mass_g,
            "density": str(self.density),
            "quantity_mode": self.mode,
        }


def _dec(value, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise DomainError("invalid_number", f"{field}: не число", field=field) from e
    if not d.is_finite() or d <= 0:
        raise DomainError("must_be_positive", f"{field}: должно быть больше нуля", field=field)
    return d


def _exact_int(value: Decimal, field: str) -> int:
    if value != value.to_integral_value():
        raise DomainError(
            "too_precise", f"{field}: слишком много знаков после запятой", field=field
        )
    return int(value)


def liters_to_ml(liters) -> int:
    return _exact_int(_dec(liters, "volume_l") * 1000, "volume_l")


def tonnes_to_g(tonnes) -> int:
    return _exact_int(_dec(tonnes, "mass_t") * 1_000_000, "mass_t")


def _check_density(density: Decimal) -> None:
    if not (DENSITY_MIN <= density <= DENSITY_MAX):
        raise DomainError(
            "density_out_of_range",
            f"Плотность {density} вне допустимого диапазона {DENSITY_MIN}–{DENSITY_MAX} кг/л",
            field="density",
        )


def compute(mode: str, *, volume_l, density=None, mass_t=None) -> Quantity:
    volume_ml = liters_to_ml(volume_l)
    if mode == QuantityMode.VOLUME_DENSITY:
        if density is None:
            raise DomainError("required", "Укажите плотность", field="density")
        dens = _dec(density, "density")
        if dens != dens.quantize(DENSITY_STEP):
            raise DomainError("too_precise", "Плотность: не более 4 знаков", field="density")
        dens = dens.quantize(DENSITY_STEP)
        _check_density(dens)
        mass_g = int((volume_ml * dens).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return Quantity(volume_ml, mass_g, dens, mode)
    if mode == QuantityMode.VOLUME_MASS:
        if mass_t is None:
            raise DomainError("required", "Укажите массу", field="mass_t")
        mass_g = tonnes_to_g(mass_t)
        dens = (Decimal(mass_g) / Decimal(volume_ml)).quantize(DENSITY_STEP, ROUND_HALF_UP)
        _check_density(dens)
        return Quantity(volume_ml, mass_g, dens, mode)
    raise DomainError("bad_quantity_mode", f"Неизвестный режим {mode}")


def fmt_liters(volume_ml: int) -> str:
    return f"{Decimal(volume_ml) / 1000:,.3f}".replace(",", " ")


def fmt_tonnes(mass_g: int) -> str:
    return f"{Decimal(mass_g) / 1_000_000:,.3f}".replace(",", " ")
