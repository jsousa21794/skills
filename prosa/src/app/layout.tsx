import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Prosa — revisão e reescrita com voz própria",
  description:
    "Transforma textos rígidos ou genéricos em textos naturais e fluidos, na tua voz, sem perder o rigor do original.",
  applicationName: "Prosa",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbfa" },
    { media: "(prefers-color-scheme: dark)", color: "#141518" },
  ],
};

// Aplica o tema antes da primeira pintura para evitar o piscar entre claro e escuro.
const themeScript = `(function(){try{var t=localStorage.getItem("prosa:theme");if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.setAttribute("data-theme",t)}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-PT" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
