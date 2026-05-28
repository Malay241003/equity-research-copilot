import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * `cn` is shadcn/ui's idiomatic class-name combiner. It accepts any mix of
 * strings, arrays, and conditional objects (via `clsx`), then de-duplicates
 * conflicting Tailwind classes (via `tailwind-merge`).
 *
 *   cn("p-4", "p-6")             // -> "p-6"          (later wins)
 *   cn("text-sm", isError && "text-red-500")
 *   cn("flex", { "items-center": align === "center" })
 *
 * Every shadcn-generated component imports this helper, so it lives at the
 * conventional path `@/lib/utils`.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
