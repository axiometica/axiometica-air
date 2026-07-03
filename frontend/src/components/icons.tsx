/**
 * Icon library — direct per-file imports.
 *
 * Imports each icon from its individual ESM file instead of the barrel
 * (@tabler/icons-react exports ~5 000 icons; the barrel forces Rollup to
 * parse every one of them even though only ~65 are used here).
 *
 * Direct imports cut Rollup's module count from ~7 300 to ~2 300,
 * reducing production build time by roughly 60%.
 *
 * Type safety comes from src/tabler-direct.d.ts which maps the wildcard
 * path pattern to @tabler/icons-react's `Icon` type.
 *
 * To add an icon: drop one line here; the file lives at
 *   node_modules/@tabler/icons-react/dist/esm/icons/<IconName>.mjs
 */

import _IconActivity          from '@tabler/icons-react/dist/esm/icons/IconActivity.mjs';
import _IconActivityHeartbeat from '@tabler/icons-react/dist/esm/icons/IconActivityHeartbeat.mjs';
import _IconAlertCircle       from '@tabler/icons-react/dist/esm/icons/IconAlertCircle.mjs';
import _IconAlertOctagon      from '@tabler/icons-react/dist/esm/icons/IconAlertOctagon.mjs';
import _IconAlertTriangle     from '@tabler/icons-react/dist/esm/icons/IconAlertTriangle.mjs';
import _IconArrowDown         from '@tabler/icons-react/dist/esm/icons/IconArrowDown.mjs';
import _IconArrowLeft         from '@tabler/icons-react/dist/esm/icons/IconArrowLeft.mjs';
import _IconArrowRight        from '@tabler/icons-react/dist/esm/icons/IconArrowRight.mjs';
import _IconArrowUp           from '@tabler/icons-react/dist/esm/icons/IconArrowUp.mjs';
import _IconBell              from '@tabler/icons-react/dist/esm/icons/IconBell.mjs';
import _IconBolt              from '@tabler/icons-react/dist/esm/icons/IconBolt.mjs';
import _IconBook              from '@tabler/icons-react/dist/esm/icons/IconBook.mjs';
import _IconBookmark          from '@tabler/icons-react/dist/esm/icons/IconBookmark.mjs';
import _IconBrain             from '@tabler/icons-react/dist/esm/icons/IconBrain.mjs';
import _IconBrandUbuntu       from '@tabler/icons-react/dist/esm/icons/IconBrandUbuntu.mjs';
import _IconBrandWindows      from '@tabler/icons-react/dist/esm/icons/IconBrandWindows.mjs';
import _IconChartBar          from '@tabler/icons-react/dist/esm/icons/IconChartBar.mjs';
import _IconChartLine         from '@tabler/icons-react/dist/esm/icons/IconChartLine.mjs';
import _IconCheck             from '@tabler/icons-react/dist/esm/icons/IconCheck.mjs';
import _IconChevronDown       from '@tabler/icons-react/dist/esm/icons/IconChevronDown.mjs';
import _IconChevronRight      from '@tabler/icons-react/dist/esm/icons/IconChevronRight.mjs';
import _IconCircleCheck       from '@tabler/icons-react/dist/esm/icons/IconCircleCheck.mjs';
import _IconCircleCheckFilled from '@tabler/icons-react/dist/esm/icons/IconCircleCheckFilled.mjs';
import _IconCircleDot         from '@tabler/icons-react/dist/esm/icons/IconCircleDot.mjs';
import _IconClipboardList     from '@tabler/icons-react/dist/esm/icons/IconClipboardList.mjs';
import _IconClock             from '@tabler/icons-react/dist/esm/icons/IconClock.mjs';
import _IconCode              from '@tabler/icons-react/dist/esm/icons/IconCode.mjs';
import _IconDatabase          from '@tabler/icons-react/dist/esm/icons/IconDatabase.mjs';
import _IconDeviceFloppy      from '@tabler/icons-react/dist/esm/icons/IconDeviceFloppy.mjs';
import _IconEye               from '@tabler/icons-react/dist/esm/icons/IconEye.mjs';
import _IconEyeOff            from '@tabler/icons-react/dist/esm/icons/IconEyeOff.mjs';
import _IconFileText          from '@tabler/icons-react/dist/esm/icons/IconFileText.mjs';
import _IconGripVertical      from '@tabler/icons-react/dist/esm/icons/IconGripVertical.mjs';
import _IconHistory           from '@tabler/icons-react/dist/esm/icons/IconHistory.mjs';
import _IconInfoCircle        from '@tabler/icons-react/dist/esm/icons/IconInfoCircle.mjs';
import _IconKey               from '@tabler/icons-react/dist/esm/icons/IconKey.mjs';
import _IconLayoutDashboard   from '@tabler/icons-react/dist/esm/icons/IconLayoutDashboard.mjs';
import _IconLoader            from '@tabler/icons-react/dist/esm/icons/IconLoader.mjs';
import _IconLoader2           from '@tabler/icons-react/dist/esm/icons/IconLoader2.mjs';
import _IconLock              from '@tabler/icons-react/dist/esm/icons/IconLock.mjs';
import _IconLogout            from '@tabler/icons-react/dist/esm/icons/IconLogout.mjs';
import _IconMenu              from '@tabler/icons-react/dist/esm/icons/IconMenu.mjs';
import _IconMessage           from '@tabler/icons-react/dist/esm/icons/IconMessage.mjs';
import _IconMoon              from '@tabler/icons-react/dist/esm/icons/IconMoon.mjs';
import _IconNetwork           from '@tabler/icons-react/dist/esm/icons/IconNetwork.mjs';
import _IconPencil            from '@tabler/icons-react/dist/esm/icons/IconPencil.mjs';
import _IconPlus              from '@tabler/icons-react/dist/esm/icons/IconPlus.mjs';
import _IconPlugConnected     from '@tabler/icons-react/dist/esm/icons/IconPlugConnected.mjs';
import _IconRadar             from '@tabler/icons-react/dist/esm/icons/IconRadar.mjs';
import _IconRefresh           from '@tabler/icons-react/dist/esm/icons/IconRefresh.mjs';
import _IconRobot             from '@tabler/icons-react/dist/esm/icons/IconRobot.mjs';
import _IconScale             from '@tabler/icons-react/dist/esm/icons/IconScale.mjs';
import _IconSearch            from '@tabler/icons-react/dist/esm/icons/IconSearch.mjs';
import _IconServer            from '@tabler/icons-react/dist/esm/icons/IconServer.mjs';
import _IconSitemap           from '@tabler/icons-react/dist/esm/icons/IconSitemap.mjs';
import _IconTags              from '@tabler/icons-react/dist/esm/icons/IconTags.mjs';
import _IconSettings          from '@tabler/icons-react/dist/esm/icons/IconSettings.mjs';
import _IconShield            from '@tabler/icons-react/dist/esm/icons/IconShield.mjs';
import _IconShieldCheck       from '@tabler/icons-react/dist/esm/icons/IconShieldCheck.mjs';
import _IconSun               from '@tabler/icons-react/dist/esm/icons/IconSun.mjs';
import _IconTestPipe          from '@tabler/icons-react/dist/esm/icons/IconTestPipe.mjs';
import _IconTool              from '@tabler/icons-react/dist/esm/icons/IconTool.mjs';
import _IconTrash             from '@tabler/icons-react/dist/esm/icons/IconTrash.mjs';
import _IconTrendingUp        from '@tabler/icons-react/dist/esm/icons/IconTrendingUp.mjs';
import _IconUsers             from '@tabler/icons-react/dist/esm/icons/IconUsers.mjs';
import _IconX                 from '@tabler/icons-react/dist/esm/icons/IconX.mjs';

export const IconActivity          = _IconActivity;
export const IconActivityHeartbeat = _IconActivityHeartbeat;
export const IconAlertCircle       = _IconAlertCircle;
export const IconAlertOctagon      = _IconAlertOctagon;
export const IconAlertTriangle     = _IconAlertTriangle;
export const IconArrowDown         = _IconArrowDown;
export const IconArrowLeft         = _IconArrowLeft;
export const IconArrowRight        = _IconArrowRight;
export const IconArrowUp           = _IconArrowUp;
export const IconBell              = _IconBell;
export const IconBolt              = _IconBolt;
export const IconBook              = _IconBook;
export const IconBookmark          = _IconBookmark;
export const IconBrain             = _IconBrain;
export const IconBrandUbuntu       = _IconBrandUbuntu;
export const IconBrandWindows      = _IconBrandWindows;
export const IconChartBar          = _IconChartBar;
export const IconChartLine         = _IconChartLine;
export const IconCheck             = _IconCheck;
export const IconChevronDown       = _IconChevronDown;
export const IconChevronRight      = _IconChevronRight;
export const IconCircleCheck       = _IconCircleCheck;
export const IconCircleCheckFilled = _IconCircleCheckFilled;
export const IconCircleDot         = _IconCircleDot;
export const IconClipboardList     = _IconClipboardList;
export const IconClock             = _IconClock;
export const IconCode              = _IconCode;
export const IconDatabase          = _IconDatabase;
export const IconDeviceFloppy      = _IconDeviceFloppy;
export const IconEye               = _IconEye;
export const IconEyeOff            = _IconEyeOff;
export const IconFileText          = _IconFileText;
export const IconGripVertical      = _IconGripVertical;
export const IconHistory           = _IconHistory;
export const IconInfoCircle        = _IconInfoCircle;
export const IconKey               = _IconKey;
export const IconLayoutDashboard   = _IconLayoutDashboard;
export const IconLoader            = _IconLoader;
export const IconLoader2           = _IconLoader2;
export const IconLock              = _IconLock;
export const IconLogout            = _IconLogout;
export const IconMenu              = _IconMenu;
export const IconMessage           = _IconMessage;
export const IconMoon              = _IconMoon;
export const IconNetwork           = _IconNetwork;
export const IconPencil            = _IconPencil;
export const IconPlus              = _IconPlus;
export const IconPlugConnected     = _IconPlugConnected;
export const IconRadar             = _IconRadar;
export const IconRefresh           = _IconRefresh;
export const IconRobot             = _IconRobot;
export const IconScale             = _IconScale;
export const IconSearch            = _IconSearch;
export const IconServer            = _IconServer;
export const IconSettings          = _IconSettings;
export const IconSitemap           = _IconSitemap;
export const IconTags              = _IconTags;
export const IconShield            = _IconShield;
export const IconShieldCheck       = _IconShieldCheck;
export const IconSun               = _IconSun;
export const IconTestPipe          = _IconTestPipe;
export const IconTool              = _IconTool;
export const IconTrash             = _IconTrash;
export const IconTrendingUp        = _IconTrendingUp;
export const IconUsers             = _IconUsers;
export const IconX                 = _IconX;
