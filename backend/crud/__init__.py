"""
crud/__init__.py — Backward-compatibility re-export layer.

Every public name from the domain submodules is re-exported here so
callers can do either:

    import crud; crud.create_user(...)          ✓
    from crud import create_user                 ✓

Implementation lives in:
    crud/users.py       — users, refresh_tokens, otp_codes
    crud/farms.py        — farms, farm_members
    crud/animals.py       — animal_batches, mortality_records, medication_records
    crud/finance.py        — feed_purchases, feed_consumption, sales, expenses, income
    crud/inventory.py       — inventory_items
"""

from crud._helpers import _now, _new_id, _one, _many  # noqa: F401

from crud.users import (  # noqa: F401
    create_user,
    get_user_by_id,
    get_user_by_email,
    get_user_by_phone,
    get_user_by_identifier,
    update_user,
    register_failed_login,
    clear_failed_logins,
    is_locked,
    create_refresh_token_row,
    get_refresh_token_by_jti,
    revoke_refresh_token,
    revoke_all_refresh_tokens,
    create_otp,
    verify_otp,
)

from crud.farms import (  # noqa: F401
    create_farm,
    get_farm,
    list_farms_for_user,
    update_farm,
    soft_delete_farm,
    add_member,
    get_membership,
    list_members,
)

from crud.animals import (  # noqa: F401
    create_batch,
    get_batch,
    list_batches,
    update_batch,
    decrement_batch_quantity,
    create_mortality_record,
    list_mortality_records,
    create_medication_record,
    list_medication_records,
)

from crud.finance import (  # noqa: F401
    create_feed_purchase,
    list_feed_purchases,
    create_feed_consumption,
    list_feed_consumption,
    feed_cost_summary,
    create_sale,
    list_sales,
    create_expense,
    list_expenses,
    create_income,
    list_income,
    profit_loss_summary,
)

from crud.inventory import (  # noqa: F401
    create_item,
    get_item,
    list_items,
    update_item,
    adjust_stock,
    delete_item,
)
