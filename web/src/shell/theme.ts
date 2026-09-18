import type { ThemeConfig } from 'antd';

/**
 * Design tokens for the whole platform.
 *
 * Everything visual comes from here or from antd's defaults — no page owns a
 * hand-written stylesheet, so a change made in one place lands everywhere.
 */
export const platformTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1677ff',
    borderRadius: 6,
    fontSize: 14,
    // Tables and log panels carry dense numeric data; give them room to breathe.
    controlHeight: 34,
  },
  components: {
    Layout: {
      headerHeight: 56,
      headerPadding: '0 24px',
    },
    Menu: {
      itemBorderRadius: 6,
      itemMarginInline: 8,
    },
    Table: {
      cellPaddingBlock: 10,
    },
  },
};
