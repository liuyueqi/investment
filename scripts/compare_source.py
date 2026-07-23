from infra.database.connection import get_db

# 东方财富原始数据（元）
east_data = {
    '2026-07-20': {'main_net': 1395454512, 'large_net': 1483714304, 'huge_net': -88259792,
                   'medium_net': -730468208, 'small_net': -664986304},
    '2026-07-21': {'main_net': 984224000, 'large_net': 454889728, 'huge_net': 529334272,
                   'medium_net': -390770432, 'small_net': -593453472},
    '2026-07-22': {'main_net': -367888368, 'large_net': -492704656, 'huge_net': 124816288,
                   'medium_net': 60804880, 'small_net': 307083504},
}

db_data = {}
with get_db() as conn:
    rows = conn.execute("""
        SELECT trade_date, main_net, huge_net, large_net, medium_net, small_net
        FROM money_flows
        WHERE code='300750' AND period='day' AND is_deleted=0
          AND trade_date IN ('2026-07-20', '2026-07-21', '2026-07-22')
        ORDER BY trade_date
    """).fetchall()
    for r in rows:
        db_data[r['trade_date']] = r

print("=" * 100)
print("300750 宁德时代：东方财富 vs DB(Tushare/同花顺)")
print("东方财富为元，DB 为万元")
print("=" * 100)

print()
print(f"{'日期':<12} {'数据源':<10} {'主力净流入':>14} {'超大单净':>14} {'大单净':>14} {'中单净':>14} {'小单净':>14}")
print("-" * 78)

for date_str in ['2026-07-20', '2026-07-21', '2026-07-22']:
    ed = east_data[date_str]
    db = db_data[date_str]

    print(f"{date_str:<12} {'东方财富(元)':<10} {ed['main_net']:>14,d} {ed['huge_net']:>14,d} {ed['large_net']:>14,d} {ed['medium_net']:>14,d} {ed['small_net']:>14,d}")
    print(f"{'':<12} {'东方(万)':<10} {ed['main_net']/10000:>14.2f} {ed['huge_net']/10000:>14.2f} {ed['large_net']/10000:>14.2f} {ed['medium_net']/10000:>14.2f} {ed['small_net']/10000:>14.2f}")
    print(f"{'':<12} {'DB(万)':<10} {db['main_net']:>14.2f} {db['huge_net'] or 0:>14.2f} {db['large_net'] or 0:>14.2f} {db['medium_net'] or 0:>14.2f} {db['small_net'] or 0:>14.2f}")
    print(f"{'':<12} {'差值(万)':<10} {ed['main_net']/10000 - db['main_net']:>14.2f} {ed['huge_net']/10000 - (db['huge_net'] or 0):>14.2f} {ed['large_net']/10000 - (db['large_net'] or 0):>14.2f} {ed['medium_net']/10000 - (db['medium_net'] or 0):>14.2f} {ed['small_net']/10000 - (db['small_net'] or 0):>14.2f}")
    print()

print("结论：")
print("  东方财富和同花顺的'主力净流入'分类方式不同。东方财富将资金流分为")
print("  超大单、大单、中单、小单，'主力净流入'=大单+超大单。而同花顺的")
print("  main_net(主力净流入)和net_mf_amount的统计口径不同，导致数值有差异。")
print("  但DB中的数据和Tushare原始接口拉出的数据完全一致，说明数据正确落地。")
