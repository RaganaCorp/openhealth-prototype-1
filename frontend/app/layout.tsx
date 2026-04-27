import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { Header } from "@/components/Header";

import "./app.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "OpenHealth",
  description: "Local-first medical document assistant",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetbrainsMono.variable} min-h-screen bg-background font-sans text-text-primary antialiased`}>
        <div className="app-background">
          <Header />
          <main className="mx-auto w-full max-w-[1560px] px-4 pb-10 pt-6 sm:px-6 lg:px-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
