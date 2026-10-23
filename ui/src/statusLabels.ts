const STATUS_LABELS: Record<string, string> = {
  MATCHED: "Abgeglichen",
  DIFFERENCE: "Abweichung",
  REVIEW_REQUIRED: "Prüfung erforderlich",
  PARTIALLY_COMPLETED: "Teilweise verarbeitet",
  EMPTY_COMPLETED: "Keine Buchungen",
  STALE: "Daten nicht aktuell",
  LOCKED: "Schreibgeschützt",
  ANALYZED: "Analysiert",
  COMPLETED: "Abgeschlossen",
  IMPORTED: "Importiert",
  SKIPPED: "Übersprungen",
  FAILED: "Fehlgeschlagen",
  ACTIVE: "Aktiv",
  ARCHIVED: "Archiviert",
  PROPOSED: "Vorgeschlagen",
  PENDING: "Offen",
  CONFIRMED: "Bestätigt",
  REJECTED: "Abgelehnt",
  NOT_RECONCILED: "Noch nicht abgeglichen",
  MISSING_BALANCE: "Saldo fehlt",
  CLOSED: "Geschlossen",
};

export function statusLabel(value: string): string {
  return STATUS_LABELS[value] ?? value.toLowerCase().replaceAll("_", " ").replace(/(^|\s)\S/g, (character) => character.toUpperCase());
}

export function statusTone(value: string): string {
  if (["MATCHED", "COMPLETED", "IMPORTED", "CONFIRMED", "ACTIVE"].includes(value)) return "confirmed";
  if (["DIFFERENCE", "REVIEW_REQUIRED", "FAILED", "STALE"].includes(value)) return "missed";
  return "paused";
}
