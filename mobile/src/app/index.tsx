import { Redirect } from "expo-router";
import { useAuth } from "@/auth/AuthContext";
import { Loading } from "@/components/ui";

/** The app's front door: sends each person to the right place. */
export default function Index() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Redirect href="/login" />;
  // The platform owner manages companies and has no company data of their own, so they get a
  // separate, smaller app.
  return <Redirect href={user.role === "super_admin" ? "/platform" : "/dashboard"} />;
}
