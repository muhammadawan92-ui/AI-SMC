"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Upload,
  Code2,
  TrendingUp,
  Split,
  Lightbulb,
  GitBranch,
  Settings,
  Zap,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", icon: BarChart3, label: "Dashboard" },
  { href: "/upload", icon: Upload, label: "Upload Center" },
  { href: "/strategy", icon: Code2, label: "Strategy Logic" },
  { href: "/backtest", icon: TrendingUp, label: "Backtest Analyzer" },
  { href: "/forward-validation", icon: Split, label: "Forward Validation" },
  { href: "/improvements", icon: Lightbulb, label: "Improvement Lab" },
  { href: "/versions", icon: GitBranch, label: "Version Manager" },
  { href: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 bg-gray-900 border-r border-gray-800 flex flex-col h-full flex-shrink-0">
      {/* Logo */}
      <div className="px-6 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center">
            <Zap size={16} className="text-white" />
          </div>
          <div>
            <div className="font-bold text-white text-sm leading-tight">EA AI Platform</div>
            <div className="text-xs text-gray-500">SMC Research System</div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ href, icon: Icon, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
                active
                  ? "bg-brand-600/20 text-brand-400 border border-brand-600/30"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
              )}
            >
              <Icon size={16} className={active ? "text-brand-400" : "text-gray-500"} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-4 border-t border-gray-800">
        <div className="flex items-center gap-2 px-2 py-2 bg-red-950/30 rounded-lg border border-red-900/40">
          <Shield size={14} className="text-red-400 flex-shrink-0" />
          <div>
            <div className="text-xs font-medium text-red-400">Live Trading</div>
            <div className="text-xs text-red-600">LOCKED</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
