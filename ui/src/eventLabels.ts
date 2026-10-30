/**
 * German presentation labels for event-sourced audit history entries.
 *
 * Technical event type strings (e.g. "ImportFileAnalyzed") are the stable,
 * immutable identity of a stored event and must never be renamed. This
 * module only maps them to human-readable text for display; it never
 * changes what is stored or how the audit trail is reconstructed, so it
 * stays identical across restarts and rebuilds.
 */

export type EventCategory =
  | "FILE"
  | "MAPPING"
  | "BALANCES"
  | "EXECUTION"
  | "RECONCILIATION"
  | "OTHER";

export interface EventLabel {
  title: string;
  description: string;
  category: EventCategory;
}

export const EVENT_CATEGORY_ORDER: EventCategory[] = [
  "FILE",
  "MAPPING",
  "BALANCES",
  "EXECUTION",
  "RECONCILIATION",
  "OTHER",
];

export const EVENT_CATEGORY_TITLES: Record<EventCategory, string> = {
  FILE: "Datei und Abschnitte",
  MAPPING: "Kontozuordnung",
  BALANCES: "Anfangs- und Endsalden",
  EXECUTION: "Abschnittsimport",
  RECONCILIATION: "Abgleiche",
  OTHER: "Weitere Ereignisse",
};

const EVENT_LABELS: Record<string, EventLabel> = {
  CategoryCreated: {
    title: "Eigene Kategorie angelegt",
    description: "Eine neue benutzerdefinierte Kategorie wurde mit stabilem internem Code angelegt.",
    category: "OTHER",
  },
  CategoryRenamed: {
    title: "Kategorie umbenannt",
    description: "Der sichtbare Name wurde geändert; bestehende Buchungszuordnungen bleiben erhalten.",
    category: "OTHER",
  },
  CategoryArchived: {
    title: "Kategorie archiviert",
    description: "Die Kategorie wird für neue Zuordnungen ausgeblendet; historische Buchungen bleiben unverändert.",
    category: "OTHER",
  },
  CategoryRestored: {
    title: "Kategorie wieder aktiviert",
    description: "Die archivierte Kategorie steht wieder für neue Zuordnungen zur Verfügung.",
    category: "OTHER",
  },
  // Datei und Abschnitte
  ImportFileAnalyzed: {
    title: "Datei lokal analysiert",
    description:
      "Die CSV wurde geprüft. Bank, Berichtsmonat und enthaltene Kontoabschnitte wurden erkannt. Zu diesem Zeitpunkt wurden noch keine Transaktionen importiert.",
    category: "FILE",
  },
  ImportSectionSkipped: {
    title: "Kontoabschnitt übersprungen",
    description:
      "Ein Kontoabschnitt wurde ausdrücklich vom Import ausgeschlossen und bleibt unberücksichtigt.",
    category: "FILE",
  },
  ImportFormatFingerprintCreated: {
    title: "Importformat erkannt",
    description: "Die technische Struktur des Exports wurde ohne Finanzinhalte als reproduzierbarer Fingerabdruck erfasst.",
    category: "FILE",
  },
  UnknownImportFormatDetected: {
    title: "Unbekanntes Importformat erkannt",
    description: "Für diesen Datei- und Abschnittsaufbau ist noch kein bestätigtes Importprofil vorhanden.",
    category: "FILE",
  },
  ImportFormatChangeDetected: {
    title: "Änderung des Importformats erkannt",
    description: "Die Exportstruktur unterscheidet sich von der bestätigten Version und muss erneut geprüft werden.",
    category: "FILE",
  },

  // Kontozuordnung
  ImportSectionMapped: {
    title: "Kontoabschnitt zugeordnet",
    description:
      "Ein erkannter Kontoabschnitt der Datei wurde einem lokalen Konto zugeordnet oder bewusst übersprungen.",
    category: "MAPPING",
  },
  ImportSectionBindingConfirmed: {
    title: "Dauerhafte Kontozuordnung bestätigt",
    description:
      "Die Zuordnung von Bank, Kontoabschnitt und lokalem Konto wurde dauerhaft gespeichert und wird künftigen Importen erneut vorgeschlagen.",
    category: "MAPPING",
  },
  ImportMappingProposed: {
    title: "Importzuordnung vorgeschlagen",
    description: "Das lokale Modell hat einen bearbeitbaren Vorschlag erzeugt; es wurden keine Finanzdaten importiert.",
    category: "MAPPING",
  },
  ImportMappingValidationCompleted: {
    title: "Importzuordnung geprüft",
    description: "Die Zuordnung wurde deterministisch gegen sämtliche Datensätze des Abschnitts geprüft.",
    category: "MAPPING",
  },
  ImportMappingConfirmed: {
    title: "Importzuordnung bestätigt",
    description: "Das geprüfte Mapping wurde ausdrücklich bestätigt und darf als Profil gespeichert werden.",
    category: "MAPPING",
  },
  ImportMappingRejected: {
    title: "Importzuordnung verworfen",
    description: "Der Vorschlag wurde verworfen und wird nicht produktiv verwendet.",
    category: "MAPPING",
  },
  ImportProfileCreated: {
    title: "Importprofil erstellt",
    description: "Aus der bestätigten Zuordnung wurde ein versioniertes, deterministisches Profil erstellt.",
    category: "MAPPING",
  },
  ImportProfileReused: {
    title: "Importprofil wiederverwendet",
    description: "Ein bestätigtes Profil wurde ohne erneuten AI-Aufruf auf den Export angewendet.",
    category: "MAPPING",
  },
  ImportProfileSuperseded: {
    title: "Importprofil ersetzt",
    description: "Eine neue Profilversion wurde aktiviert; die ältere Version bleibt für historische Importe erhalten.",
    category: "MAPPING",
  },

  // Anfangs- und Endsalden
  OpeningBalanceRecorded: {
    title: "Anfangssaldo erfasst",
    description:
      "Der Kontostand zu Beginn des Berichtszeitraums wurde gespeichert — automatisch aus dem Vormonat übernommen oder manuell bestätigt.",
    category: "BALANCES",
  },
  ClosingBalanceRecorded: {
    title: "Endsaldo erfasst",
    description:
      "Der von der Bank gemeldete Kontostand am Ende des Berichtszeitraums wurde gespeichert.",
    category: "BALANCES",
  },
  OpeningBalanceCarryForwardAdjusted: {
    title: "Anfangssaldo manuell angepasst",
    description:
      "Der automatisch vorgeschlagene Anfangssaldo wurde vor der Bestätigung geändert. Der vorgeschlagene und der eingegebene Wert sowie die Begründung bleiben dauerhaft nachvollziehbar; der Endsaldo des Vormonats bleibt unverändert.",
    category: "BALANCES",
  },
  OpeningSecurityPositionRecorded: {
    title: "Anfangsposition erfasst",
    description:
      "Der Wertpapierbestand zu Beginn des Berichtszeitraums wurde je Wertpapier gespeichert.",
    category: "BALANCES",
  },
  ClosingSecurityPositionRecorded: {
    title: "Endposition erfasst",
    description:
      "Der von der Bank gemeldete Wertpapierbestand am Ende des Berichtszeitraums wurde gespeichert.",
    category: "BALANCES",
  },
  EmptyOpeningSecurityPositionsConfirmed: {
    title: "Leerer Anfangsbestand bestätigt",
    description:
      "Es wurde ausdrücklich bestätigt, dass vor dem Berichtsmonat keine Wertpapierpositionen bestanden.",
    category: "BALANCES",
  },
  SecurityPositionSnapshotCorrected: {
    title: "Wertpapierposition korrigiert",
    description:
      "Eine bereits erfasste Position wurde mit Begründung korrigiert; der vorherige Bestand bleibt im Verlauf sichtbar.",
    category: "BALANCES",
  },

  // Abschnittsimport
  ImportBatchStarted: {
    title: "Importstapel gestartet",
    description: "Ein Stapel von Rohbuchungen aus der Datei wurde zur Verarbeitung übernommen.",
    category: "EXECUTION",
  },
  RawTransactionImported: {
    title: "Rohbuchung gespeichert",
    description:
      "Eine unveränderte Buchungszeile aus der Datei wurde unverändert als Ursprungsdatensatz gespeichert.",
    category: "EXECUTION",
  },
  TransactionNormalized: {
    title: "Buchung normalisiert",
    description: "Aus der Rohbuchung wurde eine einheitliche, kategorisierbare Transaktion erzeugt.",
    category: "EXECUTION",
  },
  SecurityTransactionNormalized: {
    title: "Wertpapierbuchung normalisiert",
    description: "Eine Kauf- oder Verkaufsbuchung aus dem Depotabschnitt wurde normalisiert gespeichert.",
    category: "EXECUTION",
  },
  EmptyImportSectionProcessed: {
    title: "Leerer Kontoabschnitt bestätigt",
    description:
      "Der Kontoabschnitt enthielt im Berichtsmonat keine Buchungen und wurde als vollständig ohne Umsätze markiert.",
    category: "EXECUTION",
  },
  ImportSectionCompleted: {
    title: "Kontoabschnitt importiert",
    description: "Alle Buchungen dieses Kontoabschnitts wurden als neue, unveränderliche Events gespeichert.",
    category: "EXECUTION",
  },
  ImportBatchCompleted: {
    title: "Importstapel abgeschlossen",
    description: "Der Stapel wurde vollständig normalisiert und für den Kontoabschnitt gespeichert.",
    category: "EXECUTION",
  },
  InvestmentFundingRelationProposed: {
    title: "Wertpapier-Finanzierung vorgeschlagen",
    description:
      "Eine Kontobuchung und eine Depotbuchung wurden als zusammengehörige Finanzierung eines Wertpapierkaufs erkannt.",
    category: "EXECUTION",
  },
  InvestmentFundingRelationConfirmed: {
    title: "Wertpapier-Finanzierung bestätigt",
    description: "Eine vorgeschlagene Finanzierungsverknüpfung wurde bestätigt.",
    category: "EXECUTION",
  },
  InvestmentFundingRelationRejected: {
    title: "Wertpapier-Finanzierung abgelehnt",
    description: "Ein Finanzierungsvorschlag wurde abgelehnt.",
    category: "EXECUTION",
  },
  InvestmentFundingRelationBroken: {
    title: "Wertpapier-Finanzierung gelöst",
    description: "Eine zuvor bestätigte Finanzierungsverknüpfung wurde wieder aufgehoben.",
    category: "EXECUTION",
  },

  // Abgleiche
  ImportedPeriodBalanceReconciled: {
    title: "Saldenabgleich durchgeführt",
    description: "Der aus den importierten Buchungen berechnete Endsaldo wurde mit dem gemeldeten Endsaldo verglichen.",
    category: "RECONCILIATION",
  },
  ImportedSecurityPositionsReconciled: {
    title: "Positionsabgleich durchgeführt",
    description: "Die berechneten Wertpapierbestände wurden mit den gemeldeten Endbeständen verglichen.",
    category: "RECONCILIATION",
  },
  BalanceDifferenceDocumented: {
    title: "Saldodifferenz dokumentiert",
    description: "Eine Abweichung zwischen berechnetem und gemeldetem Saldo wurde mit einer Begründung festgehalten.",
    category: "RECONCILIATION",
  },

  // Konten, Vermögen und Verbindlichkeiten (außerhalb des Importverlaufs)
  AccountCreated: {
    title: "Konto angelegt",
    description: "Ein neues lokales Konto wurde mit Typ, Institut und Währung angelegt.",
    category: "OTHER",
  },
  AccountUpdated: {
    title: "Konto aktualisiert",
    description: "Stammdaten eines bestehenden Kontos wurden geändert.",
    category: "OTHER",
  },
  AccountClosed: {
    title: "Konto geschlossen",
    description: "Ein Konto wurde geschlossen und nimmt keine weiteren Buchungen mehr an.",
    category: "OTHER",
  },
  BalanceSnapshotRecorded: {
    title: "Saldo-Snapshot erfasst",
    description: "Ein gemeldeter oder berechneter Kontostand wurde als unveränderlicher Snapshot gespeichert.",
    category: "OTHER",
  },
  BalanceSnapshotCorrected: {
    title: "Saldo-Snapshot korrigiert",
    description: "Ein Snapshot wurde mit Begründung durch einen neuen ersetzt; der vorherige Wert bleibt sichtbar.",
    category: "OTHER",
  },
  AccountBalanceReconciled: {
    title: "Kontosaldo abgeglichen",
    description: "Der berechnete Saldo wurde mit dem zuletzt gemeldeten Saldo verglichen.",
    category: "OTHER",
  },
  AssetSnapshotRecorded: {
    title: "Vermögenswert erfasst",
    description: "Der Wert eines Vermögensgegenstands wurde zu einem Stichtag gespeichert.",
    category: "OTHER",
  },
  AssetSnapshotCorrected: {
    title: "Vermögenswert korrigiert",
    description: "Ein erfasster Vermögenswert wurde mit Begründung korrigiert.",
    category: "OTHER",
  },
  LiabilitySnapshotRecorded: {
    title: "Verbindlichkeit erfasst",
    description: "Der Wert einer Verbindlichkeit wurde zu einem Stichtag gespeichert.",
    category: "OTHER",
  },
  LiabilitySnapshotCorrected: {
    title: "Verbindlichkeit korrigiert",
    description: "Eine erfasste Verbindlichkeit wurde mit Begründung korrigiert.",
    category: "OTHER",
  },

  // Klassifikation
  TransactionClassificationProposed: {
    title: "Kategorie vorgeschlagen",
    description: "Für eine Buchung wurde automatisch eine Kategorie vorgeschlagen.",
    category: "OTHER",
  },
  TransactionClassificationConfirmed: {
    title: "Kategorie bestätigt",
    description: "Die vorgeschlagene oder manuell gewählte Kategorie wurde bestätigt.",
    category: "OTHER",
  },
  TransactionClassificationRejected: {
    title: "Kategorie abgelehnt",
    description: "Ein Kategorievorschlag wurde abgelehnt.",
    category: "OTHER",
  },
  ClassificationRuleCreated: {
    title: "Klassifikationsregel angelegt",
    description: "Eine Regel zur automatischen Kategorisierung künftiger Buchungen wurde gespeichert.",
    category: "OTHER",
  },
  ClassificationRuleApplied: {
    title: "Klassifikationsregel angewendet",
    description: "Eine bestehende Regel wurde automatisch auf eine neue Buchung angewendet.",
    category: "OTHER",
  },
  ClassificationMemoryRecorded: {
    title: "Klassifikation gemerkt",
    description: "Eine bestätigte Nutzerentscheidung wurde als lokales Erfahrungswissen gespeichert.",
    category: "OTHER",
  },
  ClassificationMemorySuperseded: {
    title: "Klassifikationserfahrung ersetzt",
    description: "Eine frühere Nutzerentscheidung wurde durch eine spätere Korrektur ersetzt.",
    category: "OTHER",
  },
  ClassificationMemoryMatched: {
    title: "Klassifikationserfahrung angewendet",
    description: "Für eine ähnliche Buchung wurde lokales Erfahrungswissen als Vorschlag verwendet.",
    category: "OTHER",
  },

  // Dubletten, Überträge, Erstattungen
  DuplicateTransactionDetected: {
    title: "Dublette erkannt",
    description: "Zwei Buchungen wurden als mögliche Dubletten erkannt.",
    category: "OTHER",
  },
  DuplicateTransactionConfirmed: {
    title: "Dublette bestätigt",
    description: "Eine erkannte Dublette wurde bestätigt und fließt nicht doppelt in Summen ein.",
    category: "OTHER",
  },
  DuplicateTransactionRejected: {
    title: "Dublette abgelehnt",
    description: "Ein Dublettenvorschlag wurde abgelehnt; beide Buchungen bleiben eigenständig.",
    category: "OTHER",
  },
  TransferMatchProposed: {
    title: "Kontoübertrag vorgeschlagen",
    description: "Zwei Buchungen wurden als möglicher Übertrag zwischen eigenen Konten erkannt.",
    category: "OTHER",
  },
  TransferMatchConfirmed: {
    title: "Kontoübertrag bestätigt",
    description: "Ein vorgeschlagener Übertrag wurde bestätigt.",
    category: "OTHER",
  },
  TransferMatchRejected: {
    title: "Kontoübertrag abgelehnt",
    description: "Ein Übertragsvorschlag wurde abgelehnt.",
    category: "OTHER",
  },
  TransferMatchBroken: {
    title: "Kontoübertrag gelöst",
    description: "Eine zuvor bestätigte Übertragsverknüpfung wurde wieder aufgehoben.",
    category: "OTHER",
  },
  RefundRelationProposed: {
    title: "Erstattung vorgeschlagen",
    description: "Eine Buchung wurde als mögliche Erstattung einer früheren Ausgabe erkannt.",
    category: "OTHER",
  },
  RefundRelationConfirmed: {
    title: "Erstattung bestätigt",
    description: "Eine vorgeschlagene Erstattungsverknüpfung wurde bestätigt.",
    category: "OTHER",
  },
  RefundRelationRejected: {
    title: "Erstattung abgelehnt",
    description: "Ein Erstattungsvorschlag wurde abgelehnt.",
    category: "OTHER",
  },

  // Wiederkehrende Muster und Prognosen
  RecurringPatternProposed: {
    title: "Wiederkehrendes Muster vorgeschlagen",
    description: "Aus wiederholten Buchungen wurde automatisch ein wiederkehrendes Zahlungsmuster erkannt.",
    category: "OTHER",
  },
  RecurringPatternConfirmed: {
    title: "Wiederkehrendes Muster bestätigt",
    description: "Ein vorgeschlagenes Muster wurde bestätigt und fließt künftig in Prognosen ein.",
    category: "OTHER",
  },
  RecurringPatternRejected: {
    title: "Wiederkehrendes Muster abgelehnt",
    description: "Ein vorgeschlagenes Muster wurde abgelehnt.",
    category: "OTHER",
  },
  RecurringPatternUpdated: {
    title: "Wiederkehrendes Muster geändert",
    description: "Betrag, Zeitfenster oder Kategorie eines bestätigten Musters wurden angepasst.",
    category: "OTHER",
  },
  RecurringPatternPaused: {
    title: "Wiederkehrendes Muster pausiert",
    description: "Ein Muster wird vorübergehend nicht mehr erwartet.",
    category: "OTHER",
  },
  RecurringPatternEnded: {
    title: "Wiederkehrendes Muster beendet",
    description: "Ein Muster wird dauerhaft nicht mehr erwartet.",
    category: "OTHER",
  },
  ExpectedTransactionCreated: {
    title: "Erwartete Buchung angelegt",
    description: "Aus einem bestätigten Muster wurde eine für den Monat erwartete Buchung abgeleitet.",
    category: "OTHER",
  },
  ExpectedTransactionMatched: {
    title: "Erwartete Buchung eingetroffen",
    description: "Eine tatsächliche Buchung wurde einer erwarteten Buchung zugeordnet.",
    category: "OTHER",
  },
  ExpectedTransactionMissed: {
    title: "Erwartete Buchung ausgeblieben",
    description: "Eine erwartete Buchung ist bis zum Fälligkeitsfenster nicht eingetroffen.",
    category: "OTHER",
  },
  ExpectedTransactionCancelled: {
    title: "Erwartete Buchung storniert",
    description: "Eine erwartete Buchung wurde storniert, etwa weil das zugrunde liegende Muster endete.",
    category: "OTHER",
  },
  ForecastCreated: {
    title: "Prognose erstellt",
    description: "Eine neue Monatsprognose wurde aus bestätigten Mustern und historischen Werten berechnet.",
    category: "OTHER",
  },
  ForecastEvaluated: {
    title: "Prognose ausgewertet",
    description: "Eine vergangene Prognose wurde mit den tatsächlichen Werten verglichen.",
    category: "OTHER",
  },
  ForecastSuperseded: {
    title: "Prognose ersetzt",
    description: "Eine Prognose wurde durch eine neu berechnete Version abgelöst.",
    category: "OTHER",
  },
  CategoryShadowModeEnabled: {
    title: "Kategorie-Shadow-Mode aktiviert",
    description: "Das lokale Modell darf geeignete Transaktionen ausschließlich zur lokalen Qualitätsmessung bewerten.",
    category: "OTHER",
  },
  CategoryShadowModeDisabled: {
    title: "Kategorie-Shadow-Mode deaktiviert",
    description: "Neue lokale Kategorievorhersagen wurden deaktiviert; vorhandene Evaluationen bleiben erhalten.",
    category: "OTHER",
  },
  CategoryEvaluationRunStarted: {
    title: "Historische Kategorie-Evaluation gestartet",
    description: "Bestätigte historische Kategorien werden lokal und ohne Übergabe der Ground Truth ausgewertet.",
    category: "OTHER",
  },
  CategoryEvaluationRunProgressed: {
    title: "Historische Kategorie-Evaluation fortgeschritten",
    description: "Der lokale Evaluationslauf hat einen weiteren Datensatz verarbeitet.",
    category: "OTHER",
  },
  CategoryEvaluationRunPaused: {
    title: "Kategorie-Evaluation pausiert",
    description: "Der lokale Evaluationslauf wurde angehalten und kann manuell fortgesetzt werden.",
    category: "OTHER",
  },
  CategoryEvaluationRunResumed: {
    title: "Kategorie-Evaluation fortgesetzt",
    description: "Ein angehaltener lokaler Evaluationslauf wurde manuell fortgesetzt.",
    category: "OTHER",
  },
  CategoryEvaluationRunCompleted: {
    title: "Kategorie-Evaluation abgeschlossen",
    description: "Der lokale Evaluationslauf wurde vollständig verarbeitet.",
    category: "OTHER",
  },
  CategoryEvaluationRunCancelled: {
    title: "Kategorie-Evaluation abgebrochen",
    description: "Der lokale Evaluationslauf wurde beendet; bereits erzeugte Ergebnisse bleiben erhalten.",
    category: "OTHER",
  },
  CategorySuggestionGenerated: {
    title: "Kategorie vorgeschlagen",
    description: "Das lokale Modell hat eine Kategorie vorgeschlagen. Die Zuordnung ist noch nicht fachlich wirksam.",
    category: "OTHER",
  },
  CategorySuggestionConfirmed: {
    title: "Kategorie bestätigt",
    description: "Der lokale AI-Vorschlag wurde vom Benutzer bestätigt und als reguläre Klassifikation gespeichert.",
    category: "OTHER",
  },
  CategorySuggestionCorrected: {
    title: "Kategorie korrigiert",
    description: "Der lokale AI-Vorschlag wurde vom Benutzer geändert und die ausgewählte Kategorie bestätigt.",
    category: "OTHER",
  },
  CategorySuggestionRejected: {
    title: "Kategorie-Vorschlag abgelehnt",
    description: "Der lokale AI-Vorschlag wurde verworfen; daraus entstand keine fachliche Klassifikation.",
    category: "OTHER",
  },
  CategorySuggestionSuperseded: {
    title: "Kategorie-Vorschlag ersetzt",
    description: "Eine neue lokale Inferenz hat den offenen Vorschlag versioniert ersetzt.",
    category: "OTHER",
  },
  ClassificationRuleCreatedFromSuggestion: {
    title: "Regel aus Kategorieentscheidung erstellt",
    description: "Der Benutzer hat ausdrücklich eine deterministische Regel aus einem geprüften AI-Vorschlag erstellt.",
    category: "OTHER",
  },
  CategorySuggestionValidationFailed: {
    title: "Shadow-Vorhersage verworfen",
    description: "Eine Modellantwort hat die technische oder fachliche Validierung nicht bestanden.",
    category: "OTHER",
  },
  CategorySuggestionEvaluated: {
    title: "Shadow-Vorhersage ausgewertet",
    description: "Die Vorhersage wurde gegen eine bestätigte Nutzerentscheidung verglichen.",
    category: "OTHER",
  },
  CategorySuggestionEvaluationSuperseded: {
    title: "Kategorie-Evaluation ersetzt",
    description: "Eine spätere Nutzerkorrektur hat eine neue, versionierte Evaluation erzeugt.",
    category: "OTHER",
  },
};

const FALLBACK_LABEL: Omit<EventLabel, "description"> = {
  title: "Unbekanntes Ereignis",
  category: "OTHER",
};

/** Always returns a safe, renderable label — unknown event types fall back cleanly. */
export function eventLabel(eventType: string): EventLabel {
  const known = EVENT_LABELS[eventType];
  if (known) return known;
  return {
    ...FALLBACK_LABEL,
    description: `Für den technischen Eventtyp „${eventType}“ liegt noch keine verständliche Beschreibung vor. Die technischen Details bleiben vollständig sichtbar.`,
  };
}

export function categoryFor(eventType: string): EventCategory {
  return eventLabel(eventType).category;
}
