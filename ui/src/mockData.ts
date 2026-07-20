import type { Envelope } from "./contracts/generated";

const envelope = <T>(data: T, state: Envelope<T>["state"] = "READY"): Envelope<T> => ({
  schema_version: "1.0.0",
  state,
  projection_sequence: 892,
  event_store_sequence: 892,
  data,
});

export function mockQuery(name: string, payload: Record<string, unknown> = {}): Envelope<unknown> {
  const month = String(payload.month ?? "2026-07");
  const responses: Record<string, Envelope<unknown>> = {
    GetCapabilityManifest: envelope({
      extension_version: "0.6.0",
      schema_version: "1.0.0",
      capabilities: {
        imports: true,
        classification: true,
        reconciliation: true,
        recurring_patterns: true,
        forecasting: true,
        forecast_evaluation: true,
        wealth: false,
        tax: false,
        receipts: false,
        cloud_sync: false,
        external_models: false,
      },
    }),
    GetRuntimeSecurityStatus: envelope({
      extension_version: "0.6.0",
      schema_version: "1.0.0",
      last_event_sequence: 892,
      checks: {
        data_path_local: "PASSED",
        git_path_protection: "PASSED",
        cloud_sync_check: "PASSED",
        keychain_available: "NOT_CHECKED",
        snapshot_loaded: "PASSED",
        snapshot_integrity: "PASSED",
        network_egress_disabled: "PASSED",
        schema_compatibility: "PASSED",
      },
    }),
    GetDashboard: envelope({
      month,
      effective_income: "4320.00",
      effective_expenses: "2598.40",
      net_cashflow: "1721.60",
      expected_month_end_surplus: "1438.20",
      remaining_expected_income: "320.00",
      remaining_expected_expenses: "603.40",
      savings_rate: "39.85",
      open_reviews: 7,
    }),
    ListTransactions: envelope({
      transactions: [
        { transaction_id: "txn_104", booking_date: "2026-07-18", amount: "-83.42", currency: "EUR", counterparty: "Markthalle Süd", description: "Lebensmittel", category_code: "FOOD_GROCERIES", duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
        { transaction_id: "txn_103", booking_date: "2026-07-16", amount: "-49.00", currency: "EUR", counterparty: "Stadtwerke Mobil", description: "Monatskarte", category_code: "MOBILITY_PUBLIC_TRANSPORT", duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
        { transaction_id: "txn_102", booking_date: "2026-07-15", amount: "320.00", currency: "EUR", counterparty: "Nebentätigkeit", description: "Honorar", category_code: "INCOME_OTHER", duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
        { transaction_id: "txn_101", booking_date: "2026-07-03", amount: "-14.99", currency: "EUR", counterparty: "Stream AG", description: "Abo Juli", category_code: "SUBSCRIPTIONS", duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
        { transaction_id: "txn_100", booking_date: "2026-07-01", amount: "4000.00", currency: "EUR", counterparty: "Beispiel GmbH", description: "Gehalt", category_code: "INCOME_SALARY", duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
      ],
    }),
    GetTransactionDetails: envelope({
      transaction_id: String(payload.transaction_id ?? ""),
      booking_date: "2026-07-18",
      amount: "-83.42",
      currency: "EUR",
      counterparty: "Markthalle Süd",
      description: "Lebensmittel",
      category_code: "FOOD_GROCERIES",
      classification: { event_type: "TransactionClassificationConfirmed" },
      reconciliation: { duplicate_status: "NONE", transfer_status: "NONE", refund_status: "NONE", cashflow_relevant: true },
      event_history: [
        { sequence_number: 820, event_type: "TransactionNormalized", occurred_at: "2026-07-18T10:24:01Z" },
        { sequence_number: 828, event_type: "TransactionClassificationConfirmed", occurred_at: "2026-07-18T10:26:14Z" },
      ],
    }),
    GetCategoryBreakdown: envelope({
      period: month,
      categories: {
        HOUSING_RENT: { category_code: "HOUSING_RENT", gross_expense: "1180.00", refund_amount: "0", effective_expense: "1180.00", transaction_count: 1 },
        FOOD_GROCERIES: { category_code: "FOOD_GROCERIES", gross_expense: "412.80", refund_amount: "0", effective_expense: "412.80", transaction_count: 8 },
        MOBILITY_PUBLIC_TRANSPORT: { category_code: "MOBILITY_PUBLIC_TRANSPORT", gross_expense: "49.00", refund_amount: "0", effective_expense: "49.00", transaction_count: 1 },
        SUBSCRIPTIONS: { category_code: "SUBSCRIPTIONS", gross_expense: "47.96", refund_amount: "0", effective_expense: "47.96", transaction_count: 3 },
        UNCLASSIFIED: { category_code: "UNCLASSIFIED", gross_expense: "27.40", refund_amount: "0", effective_expense: "27.40", transaction_count: 2 },
      },
    }),
    ListClassificationReviews: envelope({ reviews: [
      { transaction_id: "txn_108", counterparty: "Café Morgen", amount: "-11.80", proposed_category: "FOOD_RESTAURANTS", confidence: "MEDIUM" },
      { transaction_id: "txn_109", counterparty: "Digital Services", amount: "-8.99", proposed_category: "SUBSCRIPTIONS", confidence: "LOW" },
    ] }),
    ListReconciliationReviews: envelope({ type: payload.type ?? "duplicates", reviews: [
      { relation_id: `rel_${String(payload.type ?? "duplicate")}_01`, title: "Mögliche Übereinstimmung", detail: "Betrag und Buchungsdatum liegen innerhalb der Policy-Toleranz.", confidence: "MEDIUM" },
    ] }),
    ListRecurringPatterns: envelope({ patterns: [
      { pattern_id: "pat_rent", merchant_key: "wohnraum eg", frequency: "MONTHLY", expected_amount: "1180.00", category_code: "HOUSING_RENT", account_id: "Giro", direction: "EXPENSE", amount_tolerance: "15.00", next_expected_date: "2026-08-01", expected_day_from: 1, expected_day_to: 3, status: "CONFIRMED", confidence: "HIGH", policy_version: "recurrence-v1" },
      { pattern_id: "pat_salary", merchant_key: "beispiel gmbh", frequency: "MONTHLY", expected_amount: "4000.00", category_code: "INCOME_SALARY", account_id: "Giro", direction: "INCOME", amount_tolerance: "50.00", next_expected_date: "2026-08-01", expected_day_from: 28, expected_day_to: 2, status: "CONFIRMED", confidence: "HIGH", policy_version: "recurrence-v1" },
      { pattern_id: "pat_stream", merchant_key: "stream ag", frequency: "MONTHLY", expected_amount: "14.99", category_code: "SUBSCRIPTIONS", account_id: "Giro", direction: "EXPENSE", amount_tolerance: "1.00", next_expected_date: "2026-08-03", expected_day_from: 2, expected_day_to: 5, status: "PROPOSED", confidence: "MEDIUM", policy_version: "recurrence-v1" },
    ] }),
    ListExpectedTransactions: envelope({ expected_transactions: [
      { expected_transaction_id: "exp_01", recurring_pattern_id: "pat_rent", merchant_key: "wohnraum eg", expected_date: "2026-08-01", expected_amount: "1180.00", amount_tolerance: "15.00", account_id: "Giro", category_code: "HOUSING_RENT", direction: "EXPENSE", status: "EXPECTED" },
      { expected_transaction_id: "exp_02", recurring_pattern_id: "pat_salary", merchant_key: "beispiel gmbh", expected_date: "2026-08-01", expected_amount: "4000.00", amount_tolerance: "50.00", account_id: "Giro", category_code: "INCOME_SALARY", direction: "INCOME", status: "MATCHED", matched_transaction_id: "txn_100", amount_deviation: "0", date_deviation: 0 },
      { expected_transaction_id: "exp_03", recurring_pattern_id: "pat_stream", merchant_key: "stream ag", expected_date: "2026-07-03", expected_amount: "14.99", amount_tolerance: "1.00", account_id: "Giro", category_code: "SUBSCRIPTIONS", direction: "EXPENSE", status: "MISSED" },
    ] }),
    GetForecast: envelope({ month, scenarios: {
      CONSERVATIVE: { forecast_id: "fc_31", scenario: "CONSERVATIVE", predicted_surplus: "1088.20", expected_income: "288.00", expected_fixed_expenses: "603.40", predicted_variable_expenses: "318.00", lower_bound: "1038.20", upper_bound: "1138.20", confidence: "MEDIUM", assumptions: ["Bestätigte aktive Muster", "Variable Ausgaben + 15 %"], source_event_sequence: 892, forecast_policy_version: "forecast-v1", status: "ACTIVE" },
      BASE: { forecast_id: "fc_32", scenario: "BASE", predicted_surplus: "1438.20", expected_income: "320.00", expected_fixed_expenses: "603.40", predicted_variable_expenses: "0.00", lower_bound: "1428.20", upper_bound: "1448.20", confidence: "MEDIUM", assumptions: ["Bestätigte aktive Muster", "Median historischer variabler Ausgaben"], source_event_sequence: 892, forecast_policy_version: "forecast-v1", status: "ACTIVE" },
      OPTIMISTIC: { forecast_id: "fc_33", scenario: "OPTIMISTIC", predicted_surplus: "1608.20", expected_income: "352.00", expected_fixed_expenses: "603.40", predicted_variable_expenses: "-138.00", lower_bound: "1598.20", upper_bound: "1618.20", confidence: "LOW", assumptions: ["Bestätigte aktive Muster", "Variable Ausgaben − 15 %"], source_event_sequence: 892, forecast_policy_version: "forecast-v1", status: "ACTIVE" },
    } }),
    ListForecastVersions: envelope({ month, versions: [
      { sequence_number: 892, event_type: "ForecastCreated", occurred_at: "2026-07-20T09:42:00Z", payload: { forecast_id: "fc_32", scenario: "BASE", predicted_surplus: "1438.20", status: "ACTIVE" } },
      { sequence_number: 861, event_type: "ForecastSuperseded", occurred_at: "2026-07-16T08:10:00Z", payload: { forecast_id: "fc_21", scenario: "BASE", predicted_surplus: "1284.00", status: "SUPERSEDED" } },
      { sequence_number: 804, event_type: "ForecastEvaluated", occurred_at: "2026-07-01T07:03:00Z", payload: { forecast_id: "fc_12", scenario: "BASE", predicted_surplus: "970.00", status: "EVALUATED" } },
    ] }),
    GetForecastEvaluation: envelope({ month, evaluations: [{
      forecast_id: "fc_12", actual_income: "3200.00", actual_expenses: "2350.00", actual_surplus: "850.00", absolute_error: "120.00", percentage_error: "14.12", expected_transactions_matched: 8, expected_transactions_missed: 1, unexpected_transactions: 2,
      component_accuracy: { recurring_income_matched: "3200.00", recurring_expenses_matched: "1490.00", predicted_variable_expenses: "780.00", actual_variable_expenses: "860.00", surplus_absolute_error: "120.00" },
    }] }),
    ListImportBatches: envelope({ imports: [
      { import_id: "imp_20260718", created_at: "2026-07-18T10:24:00Z", parser_version: "generic-v1", status: "IMPORTED", content_hash: "a421…9bf0" },
      { import_id: "imp_20260702", created_at: "2026-07-02T08:03:00Z", parser_version: "generic-v1", status: "IMPORTED", content_hash: "c038…7a12" },
    ] }),
  };
  return responses[name] ?? envelope(null, "EMPTY");
}
