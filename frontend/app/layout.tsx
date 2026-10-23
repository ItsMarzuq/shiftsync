import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import PwaRegister from "./pwa-register";
import "./styles.css";

export const metadata: Metadata = {
  title: "ShiftSync",
  description: "Upload a roster and sync work shifts to Google Calendar",
  applicationName: "ShiftSync",
  appleWebApp: { capable: true, title: "ShiftSync", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#16181d",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <PwaRegister />
        {children}
      </body>
    </html>
  );
}
