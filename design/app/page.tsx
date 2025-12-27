"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"

export default function Home() {
  const [isHovering, setIsHovering] = useState(false)

  return (
    <main className="w-full">
      {/* Hero Section */}
      <section className="relative w-full h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 flex items-center justify-center overflow-hidden">
        {/* Main Content */}
        <div className="relative z-10 flex flex-col items-center justify-center text-center px-4 max-w-4xl">
          <h1 className="text-6xl lg:text-7xl font-bold text-white mb-6 text-pretty leading-tight">
            Experience the Future
          </h1>
          <p className="text-xl lg:text-2xl text-slate-300 mb-12 text-pretty max-w-2xl">
            Get instant answers and real-time support powered by advanced AI
          </p>
          <Button size="lg" className="px-8 py-6 text-lg">
            Get Started
          </Button>
        </div>

        {/* Small AI Agent Card - 15% of hero width */}
        <div
          className="absolute bottom-12 right-8 w-[15%] max-w-xs transition-all duration-300"
          onMouseEnter={() => setIsHovering(true)}
          onMouseLeave={() => setIsHovering(false)}
        >
          <div className="relative bg-slate-900/50 backdrop-blur-sm border border-slate-700 rounded-3xl p-4 hover:bg-slate-900/70 transition-all duration-300">
            {/* Glowing Gradient Circle */}
            <div className="relative w-full aspect-square mb-3">
              {/* Outer Glow Effect */}
              <div className="absolute inset-0 rounded-full bg-gradient-to-br from-red-500 via-yellow-500 to-green-500 blur-2xl opacity-60 animate-pulse"></div>

              {/* Inner Circle with darker gradient */}
              <div className="absolute inset-1 rounded-full bg-slate-900 flex items-center justify-center">
                <div className="absolute inset-0 rounded-full bg-gradient-to-br from-red-500 via-yellow-500 to-green-500 opacity-30 blur-lg"></div>
                {isHovering && (
                  <div className="relative z-10 text-center">
                    <div className="w-3 h-3 bg-cyan-400 rounded-full mx-auto mb-1"></div>
                    <p className="text-[10px] text-white font-medium">AI Agent</p>
                  </div>
                )}
              </div>
            </div>

            {/* Button Text */}
            <div className="text-center space-y-1">
              <p className="text-xs text-white font-semibold truncate">Ask AI</p>
              <p className="text-[9px] text-slate-400 line-clamp-1">Get answers</p>
            </div>

            {/* Hover CTA Button */}
            {isHovering && (
              <button className="w-full mt-2 bg-slate-800 hover:bg-slate-700 text-white text-xs py-2 rounded-lg transition-colors duration-200 flex items-center justify-center gap-1">
                <span className="w-2 h-2 bg-cyan-400 rounded-full"></span>
                Chat
              </button>
            )}
          </div>
        </div>

        {/* Decorative Background Elements */}
        <div className="absolute top-0 left-0 w-96 h-96 bg-blue-500 rounded-full mix-blend-multiply filter blur-3xl opacity-5 animate-blob"></div>
        <div className="absolute top-0 right-0 w-96 h-96 bg-purple-500 rounded-full mix-blend-multiply filter blur-3xl opacity-5 animate-blob animation-delay-2000"></div>
      </section>
    </main>
  )
}
