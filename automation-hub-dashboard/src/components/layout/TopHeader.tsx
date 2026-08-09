import Icon from "../common/Icon";
import HeaderControls from "./HeaderControls";
import NotificationBell from "./NotificationBell";
import ProfileMenu from "./ProfileMenu";
import { useApp } from "../../app-context";

interface TopHeaderProps {
  onToggleSidebar: () => void;
  title?: string;
}

export default function TopHeader({ onToggleSidebar, title = "Dashboard" }: TopHeaderProps) {
  const app = useApp();

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="icon-btn" onClick={onToggleSidebar} aria-label="Toggle menu">
          <Icon name="menu" size={20} />
        </button>
        <h1 className="page-title">{title}</h1>
      </div>

      {/* Read-only summary of active Trading Instances. Instance configuration
          and lifecycle actions live on the Trading Instances page. */}
      <HeaderControls />

      <div className="topbar-right">
        <NotificationBell />
        <button className="icon-btn" aria-label="Settings" onClick={() => app.go("Settings")}>
          <Icon name="help" size={18} />
        </button>
        <ProfileMenu />
      </div>
    </header>
  );
}
