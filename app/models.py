from functools import wraps
import inspect
import numpy as np


class InversionData:
    def __init__(self, data):
        self.data = data if isinstance(data, list) else [data]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return f"InversionData({self.data})"

    def __getitem__(self, index):
        if isinstance(index, list):
            return InversionData([self.data[i] for i in index])
        return self.data[index]

    def __setitem__(self, index, value):
        self.data[index] = value

    def __array__(self, dtype=None, copy=None):
        return np.array(self.data, dtype=dtype)

    def __bands__(self):
        lens = [len(self.data[i]) for i in range(len(self))]
        return lens[0] if len(set(lens)) <= 1 else lens

    def array(self):
        return np.array(self.data)


class OutputData:
    def __init__(self, statevec, solution, uncertainty):
        self.statevec = statevec
        self.solution = solution
        self.uncertainty = uncertainty 

    def __iter__(self):
        return iter(self.solution)

    def __len__(self):
        return len(self.solution)

    def __repr__(self):
        return f"OutputData:\nNumber of rows: {len(self)}"

    def __getitem__(self, index):
        if isinstance(index, list):
            return {
                "statevec": InversionData([self.statevec[i] for i in index]),
                "solution": InversionData([self.solution[i] for i in index]),
                "uncertainty": InversionData([self.uncertainty[i] for i in index]),
            }
        return {
            "statevec": self.statevec[index],
            "solution": self.solution[index],
            "uncertainty": self.uncertainty[index],
        }

    def __setitem__(self, index, value):
        self.statevec[index] = value[0]
        self.solution[index] = value[1]
        self.uncertainty[index] = value[2]


def enforce_annotations(func):
    hints = inspect.get_annotations(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = inspect.signature(func).bind(*args, **kwargs)
        new_args = {
            name: hints[name](value)
            if (name in hints and not isinstance(value, hints[name]))
            else value
            for name, value in bound.arguments.items()
        }
        return func(**new_args)
    return wrapper
