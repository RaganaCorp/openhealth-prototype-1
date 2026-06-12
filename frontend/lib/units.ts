// Canonical storage is cm / kg. These helpers convert to and from the units a
// user prefers to type in; only the canonical value is ever persisted.

const CM_PER_INCH = 2.54;
const INCHES_PER_FOOT = 12;
const KG_PER_LB = 0.45359237;

export function feetInchesToCm(feet: number, inches: number): number {
  return (feet * INCHES_PER_FOOT + inches) * CM_PER_INCH;
}

export function cmToFeetInches(cm: number): { feet: number; inches: number } {
  const totalInches = cm / CM_PER_INCH;
  let feet = Math.floor(totalInches / INCHES_PER_FOOT);
  let inches = Math.round(totalInches - feet * INCHES_PER_FOOT);
  // Rounding can push inches to 12; carry into feet.
  if (inches === INCHES_PER_FOOT) {
    feet += 1;
    inches = 0;
  }
  return { feet, inches };
}

export function lbsToKg(lbs: number): number {
  return lbs * KG_PER_LB;
}

export function kgToLbs(kg: number): number {
  return kg / KG_PER_LB;
}

export function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** Format a canonical cm value for display in the given unit. */
export function formatHeight(cm: number, unit: "ftin" | "cm"): string {
  if (unit === "cm") {
    return `${round1(cm)} cm`;
  }
  const { feet, inches } = cmToFeetInches(cm);
  return `${feet}′ ${inches}″`;
}

/** Format a canonical kg value for display in the given unit. */
export function formatWeight(kg: number, unit: "lbs" | "kg"): string {
  if (unit === "kg") {
    return `${round1(kg)} kg`;
  }
  return `${Math.round(kgToLbs(kg))} lbs`;
}
