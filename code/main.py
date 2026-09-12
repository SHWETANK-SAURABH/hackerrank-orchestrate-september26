"""Buy or Wait? -- entry point.

Phase 1+2 status: this loads and validates the dataset, runs a
deterministic smoke test of the data-loading and currency layers, then
builds and validates per-user FinancialState for a handful of
representative users. It does NOT make any affordability decision and
does NOT write output.csv. Later phases will extend main() to produce
real predictions.

Run with:

    python3 code/main.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import evidence
import output_pipeline
import spending_changes
import verifier
from currency import CurrencyConverter
from data_loader import DataError, DataStore
from decision_engine import make_decision
from financial_state import build_financial_state
from forecast import build_forecast, can_safely_pay, describe_payment_safety, maximum_safe_payment


def _print_header(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _print_counts(store: DataStore) -> None:
    _print_header("Record counts")
    for name, count in store.summary().items():
        print(f"  {name:<16} {count}")
    drift = store.count_drift_notes()
    if drift:
        print("  (informational -- differs from Phase 0 counts)")
        for note in drift:
            print(f"    * {note}")


def _print_warnings(store: DataStore) -> None:
    if store.warnings:
        _print_header(f"Loader warnings ({len(store.warnings)})")
        for w in store.warnings:
            print(f"  ! {w}")
    if store.integrity_report.warnings:
        _print_header(f"Integrity warnings ({len(store.integrity_report.warnings)})")
        for w in store.integrity_report.warnings:
            print(f"  ! {w}")


def _describe_sample_request(store: DataStore, request_id: str) -> None:
    sample = store.get_sample_request(request_id)
    if sample is None:
        print(f"  {request_id}: not found among sample_requests.csv")
        return

    profile = store.get_profile(sample.user_id)
    events = store.get_events_for_user(sample.user_id)
    options = store.get_payment_options(sample.request_id)
    messages = store.get_messages_for_request(sample.request_id) + store.get_messages_for_user(sample.user_id)
    images = store.get_images_for_request(sample.request_id)

    print(f"  {sample.request_id} (user {sample.user_id}, {sample.request_type}):")
    print(f"    requested_amount        = {sample.requested_amount} {profile.home_currency if profile else '?'}")
    print(f"    profile found           = {profile is not None}")
    print(f"    financial events        = {len(events)}")
    print(f"    payment options         = {len(options)}")
    print(f"    messages (request+user) = {len(messages)}")
    print(f"    images (request)        = {len(images)}")
    print(f"    solved affordability    = {sample.affordability_status} / {sample.recommended_payment_method}")


def _demonstrate_blank_amount_event(store: DataStore) -> None:
    _print_header("Blank-amount event -> image resolution (not resolved yet)")
    blank = next((e for e in store.events if e.amount is None), None)
    if blank is None:
        print("  No blank-amount event found in this dataset.")
        return
    print(f"  event {blank.event_id} ({blank.description!r}): amount is None -> {blank.amount is None}")
    images = store.get_images_for_event(blank.event_id)
    if not images:
        print("  ! expected a linked image but found none (should have failed integrity checks)")
        return
    image = images[0]
    print(f"  linked image_id={image.image_id}, path={image.image_path}, exists={image.image_path.exists()}")
    print("  (amount extraction from the image is out of scope for Phase 1)")


def _demonstrate_currency_conversion(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Foreign-currency event -> home-currency conversion")
    foreign = None
    for ev in store.events:
        profile = store.get_profile(ev.user_id)
        if profile and ev.currency != profile.home_currency and ev.amount is not None:
            foreign = (ev, profile)
            break
    if foreign is None:
        print("  No foreign-currency event found in this dataset.")
        return
    ev, profile = foreign
    on_date = ev.settlement_date or ev.event_date
    converted = converter.convert(ev.amount, ev.currency, profile.home_currency, on_date)
    print(f"  event {ev.event_id} ({ev.description!r}): {ev.amount} {ev.currency} "
          f"on {on_date} -> {converted} {profile.home_currency}")

    direct_rate = converter.get_rate(on_date, ev.currency, profile.home_currency)
    print(f"  rate used ({ev.currency}->{profile.home_currency} on {on_date}): {direct_rate}")


def _demonstrate_direct_and_inverse_lookup(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Direct vs. inverse exchange-rate lookup")
    if not store.exchange_rates:
        print("  No exchange rates loaded.")
        return
    sample_rate = store.exchange_rates[0]
    direct = converter.get_rate(sample_rate.rate_date, sample_rate.from_currency, sample_rate.to_currency)
    print(f"  direct  {sample_rate.from_currency}->{sample_rate.to_currency} on {sample_rate.rate_date} = {direct}")

    inverse = converter.get_rate(sample_rate.rate_date, sample_rate.to_currency, sample_rate.from_currency)
    print(f"  inverse {sample_rate.to_currency}->{sample_rate.from_currency} on {sample_rate.rate_date} = {inverse}")
    check = (direct * inverse).quantize(Decimal("0.0000000001"))
    print(f"  direct * inverse ~= 1 (sanity check): {check}")

    try:
        converter.get_rate(sample_rate.rate_date, "ZZZ", sample_rate.to_currency)
    except Exception as exc:  # noqa: BLE001 - demonstration only
        print(f"  unsupported currency correctly rejected: {type(exc).__name__}: {exc}")


def _print_financial_state_diagnostics(store: DataStore, converter: CurrencyConverter, user_id: str) -> None:
    state = build_financial_state(store, converter, user_id)
    print(f"  user {user_id}:")
    print(f"    home_currency                = {state.home_currency}")
    print(f"    current_available_balance    = {state.current_available_balance} {state.home_currency}")
    print(f"    minimum_balance_to_keep      = {state.minimum_balance_to_keep} {state.home_currency}")
    print(f"    effective events             = {len(state.effective_events)}")
    print(f"    unresolved-amount events     = {len(state.unresolved_amount_events)}")
    print(f"    recurring income patterns    = {len(state.recurring_income_patterns)}")
    print(f"    recurring expense patterns   = {len(state.recurring_expense_patterns)}")
    print(f"    pending events               = {len(state.pending_events)}")
    print(f"    future income events         = {len(state.future_income_events)}")
    print(f"    future expense events        = {len(state.future_expense_events)}")
    print(f"    cancelled/failed events      = {len(state.cancelled_or_failed_events)}")
    print(f"    non-cash events              = {len(state.non_cash_events)}")
    print(f"    linked-event issues          = {len(state.linked_event_issues)}")
    print(f"    conflict-resolution records  = {len(state.conflict_resolutions)}")
    print(f"    warnings                     = {len(state.warnings)}")

    for n in state.unresolved_amount_events:
        assert n.amount_home_currency is None, "a blank amount must never become zero"


def _demonstrate_recurring_pattern(store: DataStore, converter: CurrencyConverter, user_id: str) -> None:
    _print_header(f"Recurring pattern from real history (user {user_id})")
    state = build_financial_state(store, converter, user_id)
    patterns = state.recurring_income_patterns + state.recurring_expense_patterns
    if not patterns:
        print(f"  No recurring pattern detected for {user_id}.")
        return
    p = sorted(patterns, key=lambda p: p.category)[0]
    print(f"  {p.direction}/{p.category} ({p.description!r}): {p.frequency.value}, "
          f"~every {p.typical_interval_days} days, typical {p.typical_amount_home_currency} {p.home_currency}, "
          f"{p.occurrence_count} occurrences, confidence={p.confidence}")
    print(f"  next_expected_occurrence = {p.next_expected_occurrence}")
    print(f"  source_event_ids[:5]     = {list(p.source_event_ids[:5])}")


def _demonstrate_linked_event_resolution(store: DataStore, converter: CurrencyConverter, user_id: str) -> None:
    _print_header(f"Linked-event resolution (user {user_id})")
    state = build_financial_state(store, converter, user_id)
    linked = [n for n in state.all_normalized_events if n.linked_info is not None]
    if not linked:
        print(f"  No linked events found for {user_id}.")
        return
    n = linked[0]
    print(f"  event {n.event_id} ({n.event_type}/{n.status}) links to {n.linked_info.linked_event_id}")
    if n.linked_info.original is not None:
        o = n.linked_info.original
        print(f"    original: {o.event_id} ({o.event_type}/{o.status})")
    print(f"    relationship = {n.linked_info.relationship.value}")
    print(f"    issues       = {n.linked_info.issues or 'none'}")


def _run_phase2_financial_state_validation(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 2: FinancialState diagnostics for representative users")
    representative_users = ["user_01", "user_03", "user_25", "user_55"]
    for uid in representative_users:
        _print_financial_state_diagnostics(store, converter, uid)

    _demonstrate_recurring_pattern(store, converter, "user_01")
    _demonstrate_linked_event_resolution(store, converter, "user_01")

    _print_header("Building FinancialState for every user (no crashes, no zeroed blanks)")
    total_unresolved = 0
    for user_id in store.profiles_by_user_id:
        state = build_financial_state(store, converter, user_id)
        for n in state.unresolved_amount_events:
            assert n.amount_home_currency is None
            total_unresolved += 1
    print(f"  built FinancialState for {len(store.profiles_by_user_id)} users")
    print(f"  total unresolved-amount events across all users = {total_unresolved}")


def _print_forecast_summary(store: DataStore, converter: CurrencyConverter, request_id: str) -> None:
    request = store.get_sample_request(request_id) or store.get_request(request_id)
    state = build_financial_state(store, converter, request.user_id)
    result = build_forecast(state, request.request_date)
    print(f"  {request.request_id} (user {request.user_id}):")
    print(f"    starting_balance         = {result.starting_balance} {result.home_currency}")
    print(f"    minimum_balance_to_keep  = {result.minimum_balance_to_keep} {result.home_currency}")
    print(f"    forecast horizon         = {result.forecast_start_date} .. {result.forecast_end_date}")
    print(f"    projected entries        = {len(result.entries)}")
    print(f"    minimum_projected_balance= {result.minimum_projected_balance} on {result.minimum_projected_balance_date}")
    print(f"    is_safe (no purchase)    = {result.is_safe}")
    print(f"    unresolved relevant ids  = {list(result.unresolved_relevant_event_ids) or 'none'}")


def _demonstrate_safe_and_unsafe_payment(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Hypothetical payment safety: one safe, one unsafe")

    safe_sample = store.get_sample_request("request_01")
    safe_state = build_financial_state(store, converter, safe_sample.user_id)
    safe_result = can_safely_pay(safe_state, safe_sample.request_date, safe_sample.request_date, safe_sample.requested_amount)
    print(f"  SAFE:   {safe_sample.request_id} paying {safe_sample.requested_amount} {safe_state.home_currency} "
          f"today -> is_safe={safe_result.is_safe}, min_balance={safe_result.minimum_projected_balance}")

    unsafe_sample = store.get_sample_request("request_15")
    unsafe_state = build_financial_state(store, converter, unsafe_sample.user_id)
    unsafe_result = can_safely_pay(
        unsafe_state, unsafe_sample.request_date, unsafe_sample.request_date, unsafe_sample.requested_amount)
    print(f"  UNSAFE: {unsafe_sample.request_id} paying {unsafe_sample.requested_amount} {unsafe_state.home_currency} "
          f"today -> is_safe={unsafe_result.is_safe}, min_balance={unsafe_result.minimum_projected_balance} "
          f"on {unsafe_result.minimum_projected_balance_date}")
    print(f"          first_violation: {unsafe_result.first_violation_date} -- {unsafe_result.violation_reason}")


def _demonstrate_future_safe_date_and_max_amount(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Future-safe-date and maximum-safe-amount demonstrations")

    sample = store.get_sample_request("request_04")
    state = build_financial_state(store, converter, sample.user_id)
    profile = describe_payment_safety(state, sample.request_date, sample.requested_amount, deadline=sample.desired_completion_date)
    print(f"  {sample.request_id}: amount={sample.requested_amount} {state.home_currency}")
    print(f"    safe_now         = {profile.safe_now}")
    print(f"    first_safe_date  = {profile.first_safe_date}")
    print(f"    safe_by_deadline = {profile.safe_by_deadline} (deadline {sample.desired_completion_date})")

    max_amount = maximum_safe_payment(state, sample.request_date, sample.request_date)
    print(f"    maximum_safe_payment on {sample.request_date} = {max_amount} {state.home_currency}")

    # Confirm the raw engine ignores payment-method preference and spending
    # changes entirely (Parts 15/16/29/30) -- it only reports facts.
    print(f"    payment_methods_user_will_consider (ignored by this engine) = "
          f"{state.payment_methods_user_will_consider}")
    print(f"    max_installment_months (ignored by this engine)             = "
          f"{state.max_installment_months}")


def _run_phase3_forecast_validation(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 3: forecast diagnostics for representative requests")
    for rid in ("request_01", "request_03", "request_05", "request_25"):
        _print_forecast_summary(store, converter, rid)

    _demonstrate_safe_and_unsafe_payment(store, converter)
    _demonstrate_future_safe_date_and_max_amount(store, converter)

    _print_header("Building a forecast for every sample request (no crashes)")
    for sample in store.sample_requests:
        state = build_financial_state(store, converter, sample.user_id)
        result = build_forecast(state, sample.request_date)
        assert result.forecast_start_date == sample.request_date
        for eid in result.unresolved_relevant_event_ids:
            assert eid  # never silently dropped -- always a real event_id
    print(f"  built a forecast for all {len(store.sample_requests)} sample requests")


def _print_representative_decisions(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 4: representative decisions")
    for rid in ("request_01", "request_02", "request_19", "request_05"):
        sample = store.get_sample_request(rid)
        state = build_financial_state(store, converter, sample.user_id)
        options = store.get_payment_options(sample.request_id)
        decision = make_decision(sample, state, options)
        print(f"  {rid} (user {sample.user_id}):")
        print(f"    generated: {decision.affordability_status} / {decision.recommended_payment_method} "
              f"amount_safe_to_pay={decision.amount_safe_to_pay} earliest={decision.earliest_date_for_full_payment}")
        print(f"    sample:    {sample.affordability_status} / {sample.recommended_payment_method} "
              f"amount_safe_to_pay={sample.amount_safe_to_pay}")
        if decision.payment_plan:
            plan_str = "|".join(f"{d}:{a}" for d, a in zip(decision.payment_plan.payment_dates, decision.payment_plan.payment_amounts))
            print(f"    plan: {plan_str}")
        invalid = [vc for vc in decision.candidates if not vc.verification.valid]
        if invalid:
            print(f"    ({len(invalid)} candidate(s) generated but rejected by the verifier)")


def _print_sample_cross_check_summary(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 4: 25-sample cross-check summary")
    status_matches = 0
    for sample in store.sample_requests:
        state = build_financial_state(store, converter, sample.user_id)
        options = store.get_payment_options(sample.request_id)
        decision = make_decision(sample, state, options)
        if (decision.affordability_status == sample.affordability_status
                and decision.recommended_payment_method == sample.recommended_payment_method):
            status_matches += 1
    print(f"  status+method exact match: {status_matches}/{len(store.sample_requests)}")
    print("  (see eval/phase4_report.md for the full per-sample table and mismatch investigation)")


def _print_250_request_diagnostic_summary(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 4: 250-request diagnostic summary")
    status_counts: dict = {}
    method_counts: dict = {}
    invalid_candidate_count = 0
    no_valid_plan_count = 0
    unresolved_evidence_count = 0
    impossible_values = 0

    for request in store.requests:
        state = build_financial_state(store, converter, request.user_id)
        options = store.get_payment_options(request.request_id)
        decision = make_decision(request, state, options)

        status_counts[decision.affordability_status] = status_counts.get(decision.affordability_status, 0) + 1
        method_counts[decision.recommended_payment_method] = method_counts.get(decision.recommended_payment_method, 0) + 1
        if decision.recommended_payment_method == "not_recommended":
            no_valid_plan_count += 1
        for vc in decision.candidates:
            if not vc.verification.valid:
                invalid_candidate_count += 1
            if vc.verification.warnings:
                unresolved_evidence_count += 1

        if decision.amount_safe_to_pay < 0 or decision.amount_safe_to_pay > request.requested_amount:
            impossible_values += 1

    print(f"  processed {len(store.requests)} requests")
    print(f"  affordability_status: {status_counts}")
    print(f"  recommended_payment_method: {method_counts}")
    print(f"  invalid candidates generated (rejected by verifier): {invalid_candidate_count}")
    print(f"  requests with no valid plan: {no_valid_plan_count}")
    print(f"  requests with unresolved evidence in horizon: {unresolved_evidence_count}")
    print(f"  impossible amount_safe_to_pay values found: {impossible_values}")


def _run_phase4_decision_engine_validation(store: DataStore, converter: CurrencyConverter) -> None:
    _print_representative_decisions(store, converter)
    _print_sample_cross_check_summary(store, converter)
    _print_250_request_diagnostic_summary(store, converter)


def _demonstrate_image_amount_resolution(store: DataStore) -> None:
    _print_header("Phase 5: image amount resolution (16 blank-amount events)")
    extractor = evidence.CachedVisionExtractor()
    facts, warnings = evidence.resolve_image_evidence(store, extractor)
    blank_events = [e for e in store.events if e.amount is None]
    print(f"  blank-amount events in dataset       = {len(blank_events)}")
    print(f"  resolved via cached image extraction = {len(facts)}")
    print(f"  left unresolved (no reliable amount) = {len(blank_events) - len(facts)}")
    fact = next(f for f in facts if f.event_id == "event_253")
    print(f"  event_253 <- {fact.source_id}: {fact.value} {fact.currency} "
          f"(confidence={fact.confidence}, no zero-amount guess ever produced)")
    for w in warnings:
        print(f"  ! {w}")


def _demonstrate_message_fact_extraction(store: DataStore) -> None:
    _print_header("Phase 5: message fact extraction (bilingual, deterministic rules)")
    facts, warnings = evidence.resolve_message_evidence(store)
    print(f"  messages in dataset                     = {len(store.messages)}")
    print(f"  facts extracted                         = {len(facts)}")
    print(f"  warnings (incl. scam/prompt-injection)  = {len(warnings)}")
    for w in warnings:
        print(f"    ! {w}")
    for rid, uid in (("request_06", "user_06"), ("request_11", "user_11"), ("request_08", "user_08")):
        user_facts = [f for f in facts if f.user_id == uid]
        print(f"  {rid} (user {uid}): {len(user_facts)} fact(s)")
        for f in user_facts:
            print(f"    {f.fact_type.value}: {f.value} {f.currency} <- {f.source_id}")


def _print_evidence_before_after(store: DataStore, converter: CurrencyConverter, bundle, request_id: str) -> None:
    request = store.get_sample_request(request_id) or store.get_request(request_id)
    options = store.get_payment_options(request.request_id)
    base_state = build_financial_state(store, converter, request.user_id)
    evidence_state, resolutions = evidence.apply_evidence(base_state, bundle, converter)
    before = make_decision(request, base_state, options)
    after = make_decision(request, evidence_state, options)
    print(f"  {request_id} (user {request.user_id}): {len(resolutions)} evidence resolution(s) applied")
    for r in resolutions:
        print(f"    - {r.description}")
    print(f"    BEFORE evidence: {before.affordability_status}/{before.recommended_payment_method} "
          f"amount_safe_to_pay={before.amount_safe_to_pay} spending_changes={before.spending_changes_needed}")
    print(f"    AFTER  evidence: {after.affordability_status}/{after.recommended_payment_method} "
          f"amount_safe_to_pay={after.amount_safe_to_pay} spending_changes={after.spending_changes_needed}")
    sample = store.get_sample_request(request_id)
    if sample:
        print(f"    SAMPLE          : {sample.affordability_status}/{sample.recommended_payment_method} "
              f"amount_safe_to_pay={sample.amount_safe_to_pay}")


def _demonstrate_spending_change_pipeline(store: DataStore, converter: CurrencyConverter) -> None:
    _print_header("Phase 5: spending-change candidate generation, re-forecast, re-verification")
    request = store.get_request("request_77")
    options = store.get_payment_options(request.request_id)
    state = build_financial_state(store, converter, request.user_id)

    plain_result = can_safely_pay(state, request.request_date, request.request_date, request.requested_amount)
    print(f"  {request.request_id} (user {request.user_id}): requested_amount={request.requested_amount} "
          f"{state.home_currency}")
    print(f"    full payment today, no spending change -> is_safe={plain_result.is_safe}")

    candidates = spending_changes.generate_spending_change_candidates(request, state)
    if not candidates:
        print("    no spending-change candidate was needed/found for this request")
        return
    plan = candidates[0]
    print(f"    generated spending-change plan: method={plan.method} spending_changes={plan.spending_changes}")

    options_by_id = {o.payment_option_id: o for o in options}
    verification = verifier.verify_plan(plan, request, state, options_by_id)
    print(f"    independent re-verification (re-forecasts the WHOLE plan as one scenario): "
          f"valid={verification.valid}")
    if verification.errors:
        for e in verification.errors:
            print(f"      ! {e}")

    decision = make_decision(request, state, options)
    print(f"    final decision via make_decision(): {decision.affordability_status}/"
          f"{decision.recommended_payment_method} spending_changes_needed={decision.spending_changes_needed}")


def _run_phase5_evidence_validation(store: DataStore, converter: CurrencyConverter) -> None:
    _demonstrate_image_amount_resolution(store)
    _demonstrate_message_fact_extraction(store)

    _print_header("Phase 5: evidence-aware decisions for the 4 required test cases")
    bundle = evidence.build_evidence_bundle(store)
    print(f"  evidence bundle: {len(bundle.facts)} fact(s), {len(bundle.warnings)} warning(s) across the dataset")
    for rid in ("request_06", "request_11", "request_08", "request_03"):
        _print_evidence_before_after(store, converter, bundle, rid)

    _demonstrate_spending_change_pipeline(store, converter)

    _print_header("Phase 5: evidence + spending-change effects across all 250 requests")
    state_cache: dict = {}

    def evidence_state_for(user_id: str):
        if user_id not in state_cache:
            base = build_financial_state(store, converter, user_id)
            adjusted, resolutions = evidence.apply_evidence(base, bundle, converter)
            state_cache[user_id] = (base, adjusted, resolutions)
        return state_cache[user_id]

    users_with_resolutions = set()
    evidence_changed_decision = 0
    spending_change_request_count = 0
    for request in store.requests:
        base_state, adjusted_state, resolutions = evidence_state_for(request.user_id)
        if resolutions:
            users_with_resolutions.add(request.user_id)
        options = store.get_payment_options(request.request_id)
        before = make_decision(request, base_state, options)
        after = make_decision(request, adjusted_state, options)
        if after.spending_changes_needed:
            spending_change_request_count += 1
        if (before.affordability_status, before.recommended_payment_method) != (
                after.affordability_status, after.recommended_payment_method):
            evidence_changed_decision += 1

    print(f"  users with >=1 evidence-driven state change              = {len(users_with_resolutions)}")
    print(f"  requests whose status/method changed due to evidence     = {evidence_changed_decision}")
    print(f"  requests whose final plan needed a spending change       = {spending_change_request_count}")
    print("  (see eval/phase5_report.md and eval/phase5_sample_comparison.md for full detail)")


def _run_phase6_production(store: DataStore, converter: CurrencyConverter, repo_root: Path) -> bool:
    """The single authoritative production run (Part 1/16): every one of
    the 250 evaluation requests through DataStore -> FinancialState ->
    EvidenceBundle -> evidence-aware State -> Forecast -> candidate plans
    -> spending-change candidates -> Verifier -> DecisionResult -> a
    validated output.csv row. Returns True iff the row-level validation
    (Part 8/11) found zero issues across all 250 rows."""
    _print_header("Phase 6: final production run (all 250 evaluation requests)")
    rows, diagnostics, issues = output_pipeline.run_production_pipeline(store, converter)

    print(f"  requests processed        = {diagnostics.total_requests}")
    print(f"  affordability_status      = {diagnostics.status_counts}")
    print(f"  recommended_payment_method= {diagnostics.method_counts}")
    print(f"  spending-change plans     = {diagnostics.spending_change_count}")
    print(f"  users with evidence applied         = {diagnostics.evidence_applied_count}")
    print(f"  requests materially changed by evidence = {diagnostics.evidence_materially_changed_count}")
    print(f"  unresolved-evidence warnings in horizon = {diagnostics.unresolved_evidence_count}")
    print(f"  invalid candidates rejected by verifier = {diagnostics.verifier_rejection_count}")
    print(f"  requests with no valid plan (not_recommended) = {diagnostics.no_valid_plan_count}")
    print(f"  runtime: total={diagnostics.total_runtime_seconds:.3f}s "
          f"state={diagnostics.state_construction_seconds:.3f}s "
          f"evidence={diagnostics.evidence_processing_seconds:.3f}s "
          f"decision={diagnostics.decision_seconds:.3f}s "
          f"validation={diagnostics.validation_seconds:.3f}s")

    out_path = repo_root / "output.csv"
    output_pipeline.write_output_csv(rows, out_path)
    print(f"  wrote {out_path}")

    _print_header("Phase 6: output.csv read-back validation")
    read_back = output_pipeline.read_output_csv(out_path)
    ids = [r["request_id"] for r in read_back]
    request_ids = {r.request_id for r in store.requests}
    row_count_ok = len(read_back) == 250
    header_ok = list(read_back[0].keys()) == list(output_pipeline.OUTPUT_COLUMNS) if read_back else False
    no_duplicates = len(ids) == len(set(ids))
    ids_match = set(ids) == request_ids
    print(f"  exactly 250 rows           = {row_count_ok} ({len(read_back)})")
    print(f"  header matches exactly     = {header_ok}")
    print(f"  no duplicate request_id    = {no_duplicates}")
    print(f"  all request IDs match requests.csv = {ids_match}")

    category_counts = {c: 0 for c in output_pipeline.VALIDATION_CATEGORIES}
    for request_issues in issues.values():
        for category, _ in request_issues:
            category_counts[category] = category_counts.get(category, 0) + 1
    print(f"  schema violations          = {category_counts['schema']}")
    print(f"  numeric violations         = {category_counts['numeric']}")
    print(f"  payment-plan violations    = {category_counts['payment_plan']}")
    print(f"  deadline violations        = {category_counts['deadline']}")
    print(f"  unsafe-plan violations     = {category_counts['unsafe_plan']}")
    print(f"  spending-change violations = {category_counts['spending_change']}")
    print(f"  other violations           = {category_counts['other']}")
    if issues:
        for rid, request_issues in list(issues.items())[:10]:
            print(f"    ! {rid}: {request_issues}")

    all_clean = (
        row_count_ok and header_ok and no_duplicates and ids_match
        and all(v == 0 for v in category_counts.values())
    )
    print(f"  FINAL VALIDATION: {'PASSED' if all_clean else 'FAILED'}")
    return all_clean


def main() -> int:
    print("Buy or Wait? -- Phase 1 data-loading smoke test")
    print(f"Repository root : {Path(__file__).resolve().parent.parent}")

    try:
        store = DataStore.load(strict=True)
    except DataError as exc:
        print("\nFAILED to load/validate the dataset:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    _print_counts(store)
    _print_warnings(store)

    _print_header("Sample request traces")
    for rid in ("request_01", "request_02", "request_06", "request_19"):
        _describe_sample_request(store, rid)

    _demonstrate_blank_amount_event(store)

    converter = CurrencyConverter.from_data_store(store)
    _demonstrate_currency_conversion(store, converter)
    _demonstrate_direct_and_inverse_lookup(store, converter)

    print()
    print("PHASE 1 SMOKE TEST: SUCCESS")
    print("(no affordability decisions were made; output.csv was not touched)")

    _run_phase2_financial_state_validation(store, converter)

    print()
    print("PHASE 2 SMOKE TEST: SUCCESS")
    print("(no affordability/forecast/payment-plan logic ran; output.csv was not touched)")

    _run_phase3_forecast_validation(store, converter)

    print()
    print("PHASE 3 SMOKE TEST: SUCCESS")
    print("(no affordability_status/amount_safe_to_pay/payment-plan decision was made; output.csv was not touched)")

    _run_phase4_decision_engine_validation(store, converter)

    print()
    print("PHASE 4 SMOKE TEST: SUCCESS")
    print("(output.csv was not generated; Phase 5 evidence/LLM/image integration was not started)")

    _run_phase5_evidence_validation(store, converter)

    print()
    print("PHASE 5 SMOKE TEST: SUCCESS")
    print("(output.csv was not generated; evidence/spending-change integration is diagnostic-only in this phase)")

    repo_root = Path(__file__).resolve().parent.parent
    validation_passed = _run_phase6_production(store, converter, repo_root)

    print()
    if validation_passed:
        print("PHASE 6 PRODUCTION RUN: SUCCESS")
    else:
        print("PHASE 6 PRODUCTION RUN: FAILED INDEPENDENT VALIDATION", file=sys.stderr)
    print(f"(output.csv written to {repo_root / 'output.csv'}; dataset/ was only ever read, never written)")
    return 0 if validation_passed else 1


if __name__ == "__main__":
    sys.exit(main())
