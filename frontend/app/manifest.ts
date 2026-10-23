import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ShiftSync",
    short_name: "ShiftSync",
    description: "Upload your work roster and sync shifts to Google Calendar.",
    start_url: "/",
    display: "standalone",
    background_color: "#f5f6f8",
    theme_color: "#16181d",
    orientation: "portrait-primary",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
  };
}
