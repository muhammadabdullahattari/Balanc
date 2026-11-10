import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
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
import { Button, ErrorNotice, Loading } from "./components/ui";
import { Auth } from "./features/auth";
import { BusinessChooser } from "./features/onboarding";
import { BusinessContext, AccessContext } from "./features/context";
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
const Loans = lazy(() =>
  import("./features/loans").then((m) => ({ default: m.Loans })),
);
const Close = lazy(() =>
  import("./features/close").then((m) => ({ default: m.Close })),
);
const Settings = lazy(() =>
  import("./features/settings").then((m) => ({ default: m.Settings })),
);
const navigation = [
  { to: "/drafts", label: "Draft review", icon: FileText },
  { to: "/refunds", label: "Payments & refunds", icon: ArrowLeftRight },
  { to: "/advances", label: "Advances", icon: Landmark },
  { to: "/rules", label: "Bank rules", icon: CheckSquare },
  { to: "/openings", label: "Opening balances", icon: BookOpen },
  { to: "/activity", label: "Activity", icon: CheckSquare },
  { to: "/search", label: "Search", icon: SearchIcon },
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
  const [businessId, setBusinessId] = useState<string>();
  const [mobile, setMobile] = useState(false);
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
    try {
      localStorage.setItem("ledgerpilot-theme", theme);
    } catch {
      /* Preference storage is optional. */
    }
  }, [theme]);
  useEffect(() => {
    setMobile(false);
    document.getElementById("main-content")?.focus();
  }, [location.pathname]);
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
  if (!businessId)
    return (
      <BusinessChooser
        identity={session.data}
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
                  paths: ["/rules", "/openings", "/activity", "/search"],
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
                  <Route path="/accounts" element={<Accounts />} />
                  <Route path="/close" element={<Close />} />
                  <Route path="/settings" element={<Settings />} />
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
          <Workspace />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
