"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React from "react";

export function Footer() {
  const currentYear = new Date().getFullYear();
  const pathname = usePathname();
  const isHome = pathname === "/";

  const navLinks = [
    { label: "Home", href: "/" },
    { label: "Services", href: isHome ? "#services" : "/#services" },
    { label: "Packages", href: isHome ? "#packages" : "/#packages" },
    { label: "AI Voices", href: "/ai-voices" },
    { label: "Contact", href: isHome ? "#contact" : "/#contact" },
  ];

  return (
    <footer className="bg-cyan-100 dark:bg-background pt-16 pb-8 px-4 md:px-6 lg:px-8 border-t border-border/60">
      <div className="max-w-7xl mx-auto">
        {/* Top Section */}
        <div className="flex flex-col md:flex-row items-center justify-between gap-8 mb-12">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-3 group">
            <span className="text-2xl font-bold text-primary dark:text-foreground tracking-tight transition-colors">
              Talk-Lee
            </span>
          </Link>

          {/* Navigation */}
          <nav>
            <ul className="flex flex-wrap items-center justify-center gap-6 md:gap-8">
              {navLinks.map((link) => (
                <li key={link.label}>
                  <Link
                    href={link.href}
                    className="text-base font-medium text-gray-700 dark:text-muted-foreground hover:text-primary dark:hover:text-foreground transition-colors relative after:content-[''] after:absolute after:left-0 after:bottom-[-4px] after:w-0 after:h-0.5 after:bg-primary after:transition-all hover:after:w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        {/* Divider */}
        <hr className="border-border/60 mb-8" />

        {/* Bottom Section */}
        <div className="flex flex-col md:flex-row items-center justify-between gap-6 text-sm">
          <div className="text-gray-700 dark:text-muted-foreground">
            © {currentYear} Talk-Lee. All rights reserved.
          </div>

          <div className="flex items-center gap-6">
            <Link 
              href="/auth/login" 
              className="font-medium text-gray-700 dark:text-muted-foreground hover:text-primary dark:hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
            >
              Login
            </Link>
            <Link 
              href="/auth/register" 
              className="font-semibold text-primary dark:text-foreground hover:text-primary/90 dark:hover:text-foreground/90 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
            >
              Start Free Trial
            </Link>
          </div>
        </div>
      </div>
    </footer>
  );
}
