import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "dark" | "light";
const KEY = "bax-theme";

/** The editorial direction is a paper one, so light is the default and
 *  an absent preference means light. Deliberately not falling back to
 *  prefers-color-scheme: a reader who has their OS in dark mode has not
 *  thereby asked this document to be dark. */
function initial(): Theme {
  return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initial);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const next = theme === "dark" ? "light" : "dark";
  const Icon = theme === "dark" ? Sun : Moon;

  return (
    <button
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--ink-3)] transition-colors hover:text-[var(--ink)]"
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
      {next === "light" ? "Light" : "Dark"}
    </button>
  );
}
