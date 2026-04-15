class ForecastTemp(float):
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

t = ForecastTemp(22.5, "dual-source")
print(t, t.source, t + 1)
