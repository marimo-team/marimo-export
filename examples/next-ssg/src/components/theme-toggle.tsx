"use client";

import { useEffect, useState } from "react";

import { isThemeChoice, themeChoices, themeStorageKey, type ThemeChoice } from "@/lib/theme";

const applyTheme = (theme: ThemeChoice): void => {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.dataset.theme = theme;
  }
};

export const ThemeToggle = () => {
  const [theme, setTheme] = useState<ThemeChoice>("system");

  useEffect(() => {
    const stored = window.localStorage.getItem(themeStorageKey);
    const initialTheme = isThemeChoice(stored) ? stored : "system";
    setTheme(initialTheme);
    applyTheme(initialTheme);
  }, []);

  const chooseTheme = (nextTheme: ThemeChoice): void => {
    setTheme(nextTheme);
    applyTheme(nextTheme);
    if (nextTheme === "system") {
      window.localStorage.removeItem(themeStorageKey);
    } else {
      window.localStorage.setItem(themeStorageKey, nextTheme);
    }
  };

  return (
    <div className="theme-toggle" aria-label="Color theme">
      {themeChoices.map((choice) => (
        <button
          key={choice}
          type="button"
          aria-pressed={theme === choice}
          data-active={theme === choice}
          onClick={() => chooseTheme(choice)}
        >
          {choice}
        </button>
      ))}
    </div>
  );
};
