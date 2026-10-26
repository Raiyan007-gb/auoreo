from enum import StrEnum


class TimeseriesApiUsageTimeseriesGetBucketType0(StrEnum):
    DAY = "day"
    HOUR = "hour"

    def __str__(self) -> str:
        return str(self.value)
