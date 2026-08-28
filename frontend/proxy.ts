import { auth } from "@/auth";
import { NextResponse } from "next/server";

// Define all routes that require authentication
const protectedRoutes = ["/dashboard", "/profile", "/courses"];

export default auth((req) => {
    const isLoggedIn = !!req.auth;
    const pathname = req.nextUrl.pathname;

    // Check if the current path starts with any of the protected routes
    const isProtectedRoute = protectedRoutes.some(route => pathname.startsWith(route));
    const isOnSignin = pathname.startsWith("/signin");

    if (isProtectedRoute && !isLoggedIn) {
        return NextResponse.redirect(new URL("/signin", req.url));
    }

    if (isOnSignin && isLoggedIn) {
        return NextResponse.redirect(new URL("/dashboard", req.url));
    }

    return NextResponse.next();
});

export const config = {
    // The matcher tells Next.js which routes this middleware should run on.
    // We include all protected paths and the signin page.
    matcher: [
        "/dashboard/:path*",
        "/profile/:path*",
        "/courses/:path*",
        "/signin"
    ],
};