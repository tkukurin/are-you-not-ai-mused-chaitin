import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "ai-muse-d chaitin'",
  description: "A group chat that builds every neighbour a different app: personal, model-editable workspaces over a shared conversation, versioned in Dolt.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
