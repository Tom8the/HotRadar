import type { Metadata } from "next";
import AuthorizationManager from "../../components/authorization-manager";

export const metadata: Metadata = { title: "采集授权 · HotRadar" };

export default function AuthorizationPage() {
  return <AuthorizationManager />;
}
