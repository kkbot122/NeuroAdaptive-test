"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { signOut } from "next-auth/react";
import LogoMark from "@/components/LogoMark";

export function CourseSidebar({ name, image }: { name: string; image?: string | null }) {
  const initial = name.trim().charAt(0).toUpperCase() || "L";
  const pathname = usePathname();
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const onSettings = pathname === "/settings";

  return <aside className="nl-dashboard-sidebar">
    <Link href="/dashboard" className="nl-dashboard-logo"><LogoMark className="nl-dashboard-logo-mark" />NeuroLearn</Link>
    <nav className="nl-dashboard-nav" aria-label="Main">
      <Link href="/dashboard" aria-current={!onSettings ? "page" : undefined}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M4 5h16v14H4zM4 10h16" /></svg>Courses</Link>
      <Link href="/settings" aria-current={onSettings ? "page" : undefined}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></svg>Settings</Link>
    </nav>
    <div className="nl-dashboard-user">
      <div className="nl-dashboard-me">
        {image && image !== failedImage
          ? <Image src={image} alt="" width={36} height={36} unoptimized className="nl-dashboard-avatar" onError={() => setFailedImage(image)} />
          : <b aria-hidden="true">{initial}</b>}
        <span>{name}</span>
      </div>
      <button type="button" className="nl-dashboard-signout" onClick={() => void signOut({ callbackUrl: "/signin" })}>Sign out</button>
    </div>
  </aside>;
}
