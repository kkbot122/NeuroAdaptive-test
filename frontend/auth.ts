import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { requireInternalToken } from "@/lib/internal-auth";

export const { handlers, signIn, signOut, auth } = NextAuth({
    providers: [
        Google({
            clientId: process.env.GOOGLE_CLIENT_ID!,
            clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
        }),
    ],
    pages: {
        signIn: "/signin",
    },
    callbacks: {
        // FIXED: Removed the broken jwt/session callbacks that called the phantom endpoint.

        async signIn({ user }) {
            if (!user.email) return false;

            try {
                // Make sure to use the server-side environment variable if possible
                const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:8000";
                
                const response = await fetch(`${backendUrl}/api/v1/auth/sync`, {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        // SECURITY: This runs on the Next.js server, so it's safe to use the secret here.
                        'x-internal-token': requireInternalToken()
                    },
                    body: JSON.stringify({
                        email: user.email,
                        full_name: user.name,
                    }),
                });

                if (!response.ok) {
                    console.error("Backend Sync Failed:", await response.text());
                    return true; // We return true to allow the frontend login to succeed even if the backend is temporarily down
                }
                
                return true;

            } catch (error) {
                console.error("Backend Sync Error:", error);
                return false;
            }
        },
    },
});