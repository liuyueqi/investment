import efinance as ef

print(hasattr(ef.stock, 'get_history_bill'))
df = ef.stock.get_history_bill('600519')
print(df.head())
print(df.columns.tolist())