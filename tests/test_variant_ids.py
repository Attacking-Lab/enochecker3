import pytest

from enochecker3 import Enochecker, InvalidVariantIdsException
from enochecker3.types import CheckerMethod


def test_missing_putflag(checker: Enochecker):
    @checker.getflag(0)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


def test_missing_getflag(checker: Enochecker):
    @checker.putflag(0)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


def test_missing_putnoise(checker: Enochecker):
    @checker.getnoise(0)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


def test_missing_getnoise(checker: Enochecker):
    @checker.putnoise(0)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


def _decorator(checker: Enochecker, method: str, variant_id: int):
    dec = getattr(checker, method)
    if method == CheckerMethod.EXPLOIT.value:
        return dec(variant_id, 0)
    return dec(variant_id)


@pytest.mark.parametrize("method", [x.value for x in CheckerMethod])
def test_duplicate_variant_id(checker, method):
    @_decorator(checker, method, 0)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):

        @_decorator(checker, method, 0)
        def bar():
            pass


@pytest.mark.parametrize("method", [x.value for x in CheckerMethod])
def test_negative_variant_id(checker, method):
    with pytest.raises(InvalidVariantIdsException):

        @_decorator(checker, method, -1)
        def foo():
            pass


@pytest.mark.parametrize("method", [x.value for x in CheckerMethod])
def test_missing_variant_id(checker, method):
    @_decorator(checker, method, 0)
    def foo():
        pass

    @_decorator(checker, method, 2)
    def bar():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


@pytest.mark.parametrize("method", [x.value for x in CheckerMethod])
def test_stacked_variant_ids(checker, method):
    @_decorator(checker, method, 0)
    @_decorator(checker, method, 1)
    def foo():
        pass

    variants = checker._method_variants[CheckerMethod(method)]
    assert variants[0] is variants[1] is foo


def _define_flagstores(checker: Enochecker, count: int) -> None:
    def noop():
        pass

    for variant_id in range(count):
        checker.putflag(variant_id)(noop)
        checker.getflag(variant_id)(noop)


def test_exploit_without_flag_variant_id(checker: Enochecker):
    with pytest.raises(TypeError):

        @checker.exploit(0)
        def foo():
            pass


def test_exploit_with_negative_flag_variant_id(checker: Enochecker):
    with pytest.raises(InvalidVariantIdsException):

        @checker.exploit(0, -1)
        def foo():
            pass


def test_exploit_with_unknown_flag_variant_id(checker: Enochecker):
    _define_flagstores(checker, 1)

    @checker.exploit(0, 1)
    def foo():
        pass

    with pytest.raises(InvalidVariantIdsException):
        checker._validate_variant_ids()


def test_exploit_variants_reported(checker: Enochecker):
    _define_flagstores(checker, 2)

    @checker.exploit(0, 1)
    def foo():
        pass

    @checker.exploit(1, 0)
    def bar():
        pass

    @checker.exploit(2, 1)
    def baz():
        pass

    assert checker.get_service_info().exploit_variants == [1, 0, 1]
