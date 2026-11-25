import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import { SpeedInsights } from "@vercel/speed-insights/react";
import {
  BrowserRouter,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  QueryClientProvider,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import {
  LayoutDashboard,
  ArrowLeftRight,
  FileText,
  Users,
  Landmark,
  FolderOpen,
  BarChart3,
  Settings as SettingsIcon,
  BookOpen,
  CheckSquare,
  Sun,
  Moon,
  Menu,
  X,
  LogOut,
  ChevronDown,
  Search as SearchIcon,
} from "lucide-react";
import {
  api,
  ApiError,
  clearSession,
  keys,
  queryClient,
  result,
} from "./api/runtime";
import { Button, ErrorNotice, Loading, Modal } from "./components/ui";
import { Auth } from "./features/auth";
import { BusinessContext, AccessContext } from "./features/context";
const BusinessChooser = lazy(() =>
  import("./features/onboarding").then((m) => ({ default: m.BusinessChooser })),
);
const AuditRecipient = lazy(() =>
  import("./features/audit-access").then((m) => ({
    default: m.AuditRecipient,
  })),
);
const Drafts = lazy(() =>
  import("./features/drafts").then((m) => ({ default: m.Drafts })),
);
const Refunds = lazy(() =>
  import("./features/refunds").then((m) => ({ default: m.Refunds })),
);
const Advances = lazy(() =>
  import("./features/advances").then((m) => ({ default: m.Advances })),
);
const Rules = lazy(() =>
  import("./features/rules").then((m) => ({ default: m.Rules })),
);
const Openings = lazy(() =>
  import("./features/openings").then((m) => ({ default: m.Openings })),
);
const Reconciliation = lazy(() =>
  import("./features/reconciliation").then((m) => ({
    default: m.Reconciliation,
  })),
);
const Ledger = lazy(() =>
  import("./features/ledger").then((m) => ({ default: m.Ledger })),
);
const Activity = lazy(() =>
  import("./features/activity").then((m) => ({ default: m.Activity })),
);
const Search = lazy(() =>
  import("./features/activity").then((m) => ({ default: m.Search })),
);
const Dashboard = lazy(() =>
  import("./features/dashboard").then((m) => ({ default: m.Dashboard })),
);
const Accounts = lazy(() =>
  import("./features/directory").then((m) => ({ default: m.Accounts })),
);
const People = lazy(() =>
  import("./features/directory").then((m) => ({ default: m.People })),
);
const Trade = lazy(() =>
  import("./features/trade").then((m) => ({ default: m.Trade })),
);
const Banking = lazy(() =>
  import("./features/banking").then((m) => ({ default: m.Banking })),
);
const Evidence = lazy(() =>
  import("./features/evidence").then((m) => ({ default: m.Evidence })),
);
const Reports = lazy(() =>
  import("./features/reports").then((m) => ({ default: m.Reports })),
);
const Prepaids = lazy(() =>
  import("./features/prepaids").then((m) => ({ default: m.Prepaids })),
);
const Projects = lazy(() =>
  import("./features/projects").then((m) => ({ default: m.Projects })),
);
const Loans = lazy(() =>
  import("./features/loans").then((m) => ({ default: m.Loans })),
);
const Close = lazy(() =>
  import("./features/close").then((m) => ({ default: m.Close })),
);
const Settings = lazy(() =>
  import("./features/settings").then((m) => ({ default: m.Settings })),
);
const P3AutomationHub = lazy(() =>
  import("./features/p3-automation").then((m) => ({
    default: m.P3AutomationHub,
  })),
);
const navigation = [
  { to: "/drafts", label: "Draft review", icon: FileText },
  { to: "/refunds", label: "Payments & refunds", icon: ArrowLeftRight },
  { to: "/advances", label: "Advances", icon: Landmark },
  { to: "/rules", label: "Bank rules", icon: CheckSquare },
  { to: "/openings", label: "Opening balances", icon: BookOpen },
  { to: "/activity", label: "Activity", icon: CheckSquare },
  { to: "/search", label: "Search", icon: SearchIcon },
  { to: "/reminders", label: "WhatsApp & Slack", icon: CheckSquare },
  { to: "/supplier-portals", label: "Supplier portals", icon: FolderOpen },
  { to: "/supplier-payments", label: "Supplier payments", icon: Landmark },
  { to: "/reconciliation", label: "Reconciliation", icon: CheckSquare },
  { to: "/ledger", label: "General ledger", icon: BookOpen },
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/banking", label: "Banking", icon: ArrowLeftRight },
  { to: "/sales", label: "Sales", icon: FileText },
  { to: "/purchases", label: "Purchases", icon: BookOpen },
  { to: "/people", label: "Contacts", icon: Users },
  { to: "/loans", label: "Loans", icon: Landmark },
  { to: "/evidence", label: "Documents", icon: FolderOpen },
  { to: "/reports", label: "Reports", icon: BarChart3 },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/prepaids", label: "Prepaid expenses", icon: BookOpen },
  { to: "/accounts", label: "Accounts", icon: BookOpen },
  { to: "/close", label: "Month-end close", icon: CheckSquare },
];
class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="fatal">
        <h1>We couldn’t open this view.</h1>
        <p>Your saved records are safe. Reload the workspace to try again.</p>
        <Button onClick={() => window.location.reload()}>
          Reload workspace
        </Button>
      </main>
    ) : (
      this.props.children
    );
  }
}
function Workspace() {
  const [onboardingReturn, setOnboardingReturn] = useState<{
    sessionId?: string;
    canceled?: boolean;
  }>(() => {
    if (typeof window === "undefined") return {};
    const params = new URLSearchParams(window.location.search);
    const state = params.get("onboarding_checkout");
    if (state === "success") {
      return { sessionId: params.get("session_id") || undefined };
    }
    if (state === "canceled") {
      return { canceled: true };
    }
    return {};
  });
  const [businessId, setBusinessId] = useState<string | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    const params = new URLSearchParams(window.location.search);
    if (params.has("onboarding_checkout")) return undefined;
    const returnedBid = params.get("business_id");
    if (returnedBid && params.has("billing")) return returnedBid;
    return undefined;
  });
  const [mobile, setMobile] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [narrow, setNarrow] = useState(
    () =>
      typeof matchMedia !== "undefined" &&
      matchMedia("(max-width: 1023px)").matches,
  );
  useEffect(() => {
    if (typeof matchMedia === "undefined") return;
    const media = matchMedia("(max-width: 1023px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!narrow) setMobile(false);
  }, [narrow]);
  const [offline, setOffline] = useState(!navigator.onLine);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  const [theme, setTheme] = useState(() => {
    try {
      return (
        localStorage.getItem("ledgerpilot-theme") ||
        (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      );
    } catch {
      return "light";
    }
  });
  const sidebarRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const closeNavigation = () => {
    setMobile(false);
    requestAnimationFrame(() => menuRef.current?.focus());
  };
  const location = useLocation();
  const finishNavigation = () => {
    if (!mobile) return;
    setMobile(false);
    requestAnimationFrame(() =>
      document.getElementById("main-content")?.focus(),
    );
  };
  useEffect(() => {
    if (!mobile) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const sidebar = sidebarRef.current;
    const links = () =>
      Array.from(
        sidebar?.querySelectorAll<HTMLElement>("a,button,summary") || [],
      ).filter((e) => e.offsetParent !== null);
    links()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMobile(false);
        requestAnimationFrame(() => menuRef.current?.focus());
      }
      if (e.key === "Tab") {
        const entries = links(),
          first = entries[0],
          last = entries.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [mobile]);
  const navigate = useNavigate();
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute(
        "content",
        getComputedStyle(document.documentElement)
          .getPropertyValue("--canvas")
          .trim(),
      );
    try {
      localStorage.setItem("ledgerpilot-theme", theme);
    } catch {
      /* Preference storage is optional. */
    }
  }, [theme]);
  useEffect(() => {
    setMobile(false);
    setPaletteOpen(false);
    document.getElementById("main-content")?.focus();
  }, [location.pathname]);
  useEffect(() => {
    if (!businessId) return;
    const onGlobalKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
        setPaletteQuery("");
        setPaletteIndex(0);
      }
    };
    window.addEventListener("keydown", onGlobalKey);
    return () => window.removeEventListener("keydown", onGlobalKey);
  }, [businessId]);
  const session = useQuery({
    queryKey: keys.session,
    queryFn: async ({ signal }) => {
      try {
        return await result(api.GET("/api/v1/auth/me", { signal }));
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
  });
  const business = useQuery({
    queryKey: businessId ? keys.business(businessId) : ["no-business"],
    enabled: !!session.data && !!businessId,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}", {
          signal,
          params: { path: { business_id: businessId! } },
        }),
      ),
  });
  const membership = useQuery({
    queryKey: ["business", businessId, "membership"],
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: 60000,
    enabled: !!session.data && !!businessId,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/membership", {
          signal,
          params: { path: { business_id: businessId! } },
        }),
      ),
  });
  useEffect(() => {
    if (session.data === null) setBusinessId(undefined);
  }, [session.data]);
  useEffect(() => {
    if (
      businessId &&
      membership.error instanceof ApiError &&
      [403, 404].includes(membership.error.status)
    ) {
      void queryClient.cancelQueries({ queryKey: keys.business(businessId) });
      queryClient.removeQueries({ queryKey: keys.business(businessId) });
      setBusinessId(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.businesses });
    }
  }, [businessId, membership.error]);
  const logout = useMutation({
    mutationFn: () => result(api.POST("/api/v1/auth/logout")),
    onSuccess: () => {
      setBusinessId(undefined);
      clearSession();
      queryClient.setQueryData(keys.session, null);
    },
  });
  const pageTitle = !session.data
    ? "Account access"
    : location.pathname === "/audit-access"
      ? "Audit access"
      : !businessId
        ? "Choose a workspace"
        : navigation.find((item) => item.to === location.pathname)?.label ||
          (location.pathname === "/settings" ? "Settings" : "Workspace");
  useEffect(() => {
    document.title = pageTitle + " · LedgerPilot";
  }, [pageTitle]);
  if (session.isPending)
    return (
      <div className="boot">
        <span className="brand-mark">L</span>
        <Loading />
      </div>
    );
  if (session.error)
    return (
      <main className="fatal">
        <h1>Let’s reconnect.</h1>
        <p>We couldn’t reach your finance workspace.</p>
        <ErrorNotice
          error={session.error}
          retry={() => {
            void session.refetch();
          }}
        />
      </main>
    );
  if (!session.data) return <Auth />;
  if (location.pathname === "/audit-access")
    return <AuditRecipient accountId={session.data.id} />;
  const clearOnboardingReturn = () => {
    setOnboardingReturn({});
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (params.has("onboarding_checkout") || params.has("session_id")) {
        params.delete("onboarding_checkout");
        params.delete("session_id");
        const clean = params.toString();
        window.history.replaceState({}, "", "/" + (clean ? `?${clean}` : ""));
      }
    }
  };
  if (!businessId)
    return (
      <BusinessChooser
        identity={session.data}
        initialOnboardingSessionId={onboardingReturn.sessionId}
        initialOnboardingCanceled={onboardingReturn.canceled}
        onClearOnboardingReturn={clearOnboardingReturn}
        onChoose={(id) => {
          setBusinessId(id);
          navigate("/");
        }}
      />
    );
  if (business.isPending) return <Loading />;
  if (business.error || !business.data)
    return (
      <main className="fatal">
        <ErrorNotice error={business.error} />
        <Button onClick={() => setBusinessId(undefined)}>
          Choose another business
        </Button>
      </main>
    );
  return (
    <AccessContext.Provider value={membership.data?.items[0] || null}>
      <BusinessContext.Provider key={business.data.id} value={business.data}>
        <div className="app-layout">
          <a href="#main-content" className="skip-link">
            Skip to content
          </a>
          {mobile && (
            <button
              className="mobile-shade"
              aria-hidden="true"
              tabIndex={-1}
              onClick={closeNavigation}
            />
          )}
          <aside
            ref={sidebarRef}
            inert={narrow && !mobile}
            aria-hidden={(narrow && !mobile) || undefined}
            className={`sidebar ${mobile ? "is-open" : ""}`}
            id="workspace-navigation"
            aria-label="Primary navigation"
          >
            <div className="sidebar-brand">
              <NavLink className="brand" to="/" onClick={finishNavigation}>
                <span className="brand-mark">L</span>LedgerPilot
              </NavLink>
              <button
                className="icon-button mobile-only"
                aria-label="Close navigation"
                onClick={closeNavigation}
              >
                <X size={20} />
              </button>
            </div>
            <button
              className="business-switch"
              onClick={async () => {
                await queryClient.cancelQueries({
                  queryKey: keys.business(businessId),
                });
                queryClient.removeQueries({
                  queryKey: keys.business(businessId),
                });
                setBusinessId(undefined);
              }}
            >
              <span className="business-avatar">
                {business.data.name.slice(0, 1).toUpperCase()}
              </span>
              <span>
                <strong>{business.data.name}</strong>
                <small>Business workspace</small>
              </span>
              <ChevronDown size={16} />
            </button>
            <span className="nav-label">WORKSPACE</span>
            <nav>
              {[
                {
                  title: "Workspace",
                  paths: [
                    "/",
                    "/banking",
                    "/sales",
                    "/purchases",
                    "/people",
                    ...(business.data.p2_prepaids_available
                      ? ["/prepaids"]
                      : []),
                    ...(business.data.p2_projects_available
                      ? ["/projects"]
                      : []),
                    "/evidence",
                    "/reports",
                  ],
                },
                {
                  title: "Accounting",
                  paths: [
                    "/ledger",
                    "/accounts",
                    "/drafts",
                    "/refunds",
                    "/advances",
                    "/loans",
                    "/reconciliation",
                    "/close",
                  ],
                },
                {
                  title: "Manage",
                  paths: [
                    "/rules",
                    "/openings",
                    "/reminders",
                    "/supplier-portals",
                    "/supplier-payments",
                    "/activity",
                    "/search",
                  ],
                },
              ].map((group) => (
                <div key={group.title} className="nav-group">
                  {group.title === "Workspace" ? (
                    group.paths.map((path) => {
                      const n = navigation.find((n) => n.to === path)!;
                      return (
                        <NavLink
                          end={path === "/"}
                          to={path}
                          key={path}
                          onClick={finishNavigation}
                        >
                          <n.icon size={18} />
                          {n.label}
                        </NavLink>
                      );
                    })
                  ) : (
                    <details open={group.paths.includes(location.pathname)}>
                      <summary>
                        {group.title}
                        <ChevronDown size={14} />
                      </summary>
                      {group.paths.map((path) => {
                        const n = navigation.find((n) => n.to === path)!;
                        return (
                          <NavLink
                            to={path}
                            key={path}
                            onClick={finishNavigation}
                          >
                            <n.icon size={18} />
                            {n.label}
                          </NavLink>
                        );
                      })}
                    </details>
                  )}
                </div>
              ))}
            </nav>
            <div className="sidebar-bottom">
              <NavLink to="/settings" onClick={finishNavigation}>
                <SettingsIcon size={18} />
                Settings
              </NavLink>
            </div>
          </aside>
          <div className="workspace" inert={mobile}>
            <header className="workspace-header">
              <div className="breadcrumb">
                <button
                  className="icon-button mobile-only"
                  ref={menuRef}
                  aria-label="Open navigation"
                  aria-expanded={mobile}
                  aria-controls="workspace-navigation"
                  onClick={() => setMobile(true)}
                >
                  <Menu size={21} />
                </button>
                <span>Workspace</span>
                <span className="divider">/</span>
                <strong>
                  {navigation.find((n) => n.to === location.pathname)?.label ||
                    "Settings"}
                </strong>
              </div>
              <div className="header-actions">
                <button
                  type="button"
                  className="command-palette-trigger"
                  aria-label="Open command palette (Ctrl+K)"
                  onClick={() => {
                    setPaletteOpen(true);
                    setPaletteQuery("");
                    setPaletteIndex(0);
                  }}
                >
                  <SearchIcon size={15} />
                  <span>Jump to…</span>
                  <kbd>Ctrl+K</kbd>
                </button>
                <NavLink
                  to="/search"
                  className="icon-button"
                  aria-label="Search workspace"
                >
                  <SearchIcon size={18} />
                </NavLink>
                <button
                  className="icon-button"
                  aria-label={
                    theme === "light"
                      ? "Switch to dark mode"
                      : "Switch to light mode"
                  }
                  onClick={() => setTheme(theme === "light" ? "dark" : "light")}
                >
                  {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
                </button>
                <span className="user-avatar" title={session.data.email}>
                  {session.data.email.slice(0, 1).toUpperCase()}
                </span>
                <button
                  className="icon-button"
                  aria-label="Sign out"
                  disabled={logout.isPending}
                  onClick={() => logout.mutate()}
                >
                  <LogOut size={18} />
                </button>
              </div>
            </header>
            <main id="main-content" className="main-content" tabIndex={-1}>
              {offline && (
                <p className="notice" role="status">
                  You’re offline. Reconnect before saving changes.
                </p>
              )}
              <ErrorNotice error={logout.error} />
              <Suspense fallback={<Loading />}>
                <Routes>
                  <Route path="/drafts" element={<Drafts />} />
                  <Route path="/refunds" element={<Refunds />} />
                  <Route path="/advances" element={<Advances />} />
                  <Route path="/rules" element={<Rules />} />
                  <Route path="/openings" element={<Openings />} />
                  <Route path="/activity" element={<Activity />} />
                  <Route path="/search" element={<Search />} />
                  <Route path="/reconciliation" element={<Reconciliation />} />
                  <Route path="/ledger" element={<Ledger />} />
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/banking" element={<Banking />} />
                  <Route
                    path="/sales"
                    element={<Trade key="sales" kind="invoice" />}
                  />
                  <Route
                    path="/purchases"
                    element={<Trade key="purchases" kind="bill" />}
                  />
                  <Route path="/people" element={<People />} />
                  <Route path="/loans" element={<Loans />} />
                  <Route path="/evidence" element={<Evidence />} />
                  <Route path="/reports" element={<Reports />} />
                  <Route path="/projects" element={<Projects />} />
                  <Route path="/prepaids" element={<Prepaids />} />
                  <Route path="/accounts" element={<Accounts />} />
                  <Route path="/close" element={<Close />} />
                  <Route
                    path="/reminders"
                    element={<P3AutomationHub initialTab="reminders" />}
                  />
                  <Route
                    path="/supplier-portals"
                    element={<P3AutomationHub initialTab="portals" />}
                  />
                  <Route
                    path="/supplier-payments"
                    element={<P3AutomationHub initialTab="payments" />}
                  />
                  <Route
                    path="/reconciliation-sla"
                    element={<P3AutomationHub initialTab="sla" />}
                  />
                  <Route
                    path="/payout-reconciliation"
                    element={<P3AutomationHub initialTab="payouts" />}
                  />
                  <Route
                    path="/voice-instructions"
                    element={<P3AutomationHub initialTab="voice" />}
                  />
                  <Route
                    path="/encryption-keys"
                    element={<P3AutomationHub initialTab="encryption" />}
                  />
                  <Route path="/settings" element={<Settings />} />
                  <Route
                    path="/settings/billing"
                    element={<Settings initialSection="billing" />}
                  />
                  <Route
                    path="*"
                    element={
                      <section className="empty">
                        <h1>This page isn’t here.</h1>
                        <NavLink to="/">Back to overview</NavLink>
                      </section>
                    }
                  />
                </Routes>
              </Suspense>
              <footer className="workspace-footer">
                <span>LedgerPilot · Your finance office</span>
                <span>
                  {business.data.currency} · {business.data.timezone}
                </span>
              </footer>
              {paletteOpen &&
                (() => {
                  const paletteItems = [
                    ...navigation
                      .filter(
                        (item) =>
                          (item.to !== "/prepaids" ||
                            business.data.p2_prepaids_available) &&
                          (item.to !== "/projects" ||
                            business.data.p2_projects_available),
                      )
                      .map((item) => ({
                        id: item.to,
                        label: item.label,
                        hint: item.to,
                        run: () => navigate(item.to),
                      })),
                    {
                      id: "/reconciliation-sla",
                      label: "3.1 · One-Day Reconciliation SLA & Auto-Chasing",
                      hint: "/reconciliation-sla",
                      run: () => navigate("/reconciliation-sla"),
                    },
                    {
                      id: "/payout-reconciliation",
                      label: "3.2 · Live Bank Feed & Gross-to-Net Payout Split",
                      hint: "/payout-reconciliation",
                      run: () => navigate("/payout-reconciliation"),
                    },
                    {
                      id: "/voice-instructions",
                      label: "3.3 · Voice-Note Accounting Instruction Review",
                      hint: "/voice-instructions",
                      run: () => navigate("/voice-instructions"),
                    },
                    {
                      id: "/encryption-keys",
                      label: "3.4 · Per-Tenant Envelope Encryption & Key Shred",
                      hint: "/encryption-keys",
                      run: () => navigate("/encryption-keys"),
                    },
                    {
                      id: "/settings",
                      label: "Settings & Integrations",
                      hint: "/settings",
                      run: () => navigate("/settings"),
                    },
                    {
                      id: "/settings/billing",
                      label: "Billing & Subscription",
                      hint: "/settings/billing",
                      run: () => navigate("/settings/billing"),
                    },
                    {
                      id: "action-theme",
                      label:
                        theme === "light"
                          ? "Switch to Dark Mode"
                          : "Switch to Light Mode",
                      hint: "Appearance",
                      run: () => setTheme(theme === "light" ? "dark" : "light"),
                    },
                    {
                      id: "action-switch-business",
                      label: "Switch Business Workspace",
                      hint: business.data.name,
                      run: () => setBusinessId(undefined),
                    },
                  ].filter((item) => {
                    const q = paletteQuery.trim().toLowerCase();
                    if (!q) return true;
                    return (
                      item.label.toLowerCase().includes(q) ||
                      item.hint.toLowerCase().includes(q)
                    );
                  });
                  const activeIdx =
                    paletteItems.length > 0
                      ? Math.min(paletteIndex, paletteItems.length - 1)
                      : 0;
                  return (
                    <Modal
                      open={true}
                      title="Quick Command Palette"
                      description="Filter and jump to any workspace view or action."
                      onClose={() => setPaletteOpen(false)}
                    >
                      <div className="command-palette">
                        <input
                          type="search"
                          aria-label="Filter workspace commands"
                          placeholder="Jump to any view or action (e.g. Supplier payments, WhatsApp, Reconciliation)…"
                          value={paletteQuery}
                          autoFocus
                          onChange={(e) => {
                            setPaletteQuery(e.target.value);
                            setPaletteIndex(0);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "ArrowDown") {
                              e.preventDefault();
                              setPaletteIndex((i) =>
                                paletteItems.length
                                  ? (i + 1) % paletteItems.length
                                  : 0,
                              );
                            } else if (e.key === "ArrowUp") {
                              e.preventDefault();
                              setPaletteIndex((i) =>
                                paletteItems.length
                                  ? (i - 1 + paletteItems.length) %
                                    paletteItems.length
                                  : 0,
                              );
                            } else if (
                              e.key === "Enter" &&
                              paletteItems[activeIdx]
                            ) {
                              e.preventDefault();
                              const chosen = paletteItems[activeIdx];
                              setPaletteOpen(false);
                              chosen.run();
                            }
                          }}
                        />
                        <div
                          className="command-palette-list"
                          role="listbox"
                          aria-label="Workspace commands"
                        >
                          {paletteItems.length === 0 ? (
                            <p className="muted" style={{ padding: "12px" }}>
                              No matching workspace views or commands.
                            </p>
                          ) : (
                            paletteItems.map((item, idx) => (
                              <button
                                type="button"
                                key={item.id}
                                role="option"
                                aria-selected={idx === activeIdx}
                                className={`command-palette-item ${idx === activeIdx ? "is-active" : ""}`}
                                onMouseEnter={() => setPaletteIndex(idx)}
                                onClick={() => {
                                  setPaletteOpen(false);
                                  item.run();
                                }}
                              >
                                <span>{item.label}</span>
                                <small>{item.hint}</small>
                              </button>
                            ))
                          )}
                        </div>
                      </div>
                    </Modal>
                  );
                })()}
            </main>
          </div>
        </div>
      </BusinessContext.Provider>
    </AccessContext.Provider>
  );
}
export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Suspense fallback={<Loading />}>
            <Workspace />
          </Suspense>
        </BrowserRouter>
      </QueryClientProvider>
      <SpeedInsights />
    </ErrorBoundary>
  );
}
