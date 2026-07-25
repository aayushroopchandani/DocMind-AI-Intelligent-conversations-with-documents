import { defaultTheme, type Theme } from "@univerjs/themes";

/**
 * DocMind-flavoured Univer theme.
 *
 * Univer's dark mode derives its dark surfaces from the theme scales, so we
 * only need to swap the primary scale to DocMind's cyan accent — selection
 * borders, focus rings and highlighted headers then match the app chrome.
 */
export const docmindUniverTheme: Theme = {
  ...defaultTheme,
  primary: {
    50: "#ecfeff",
    100: "#cffafe",
    200: "#a5f3fc",
    300: "#67e8f9",
    400: "#22d3ee",
    500: "#06b6d4",
    600: "#0891b2",
    700: "#0e7490",
    800: "#155e75",
    900: "#164e63",
  },
};
