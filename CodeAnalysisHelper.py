# %% 帮助函数
from typing import overload
from clang.cindex import Cursor, StorageClass, Type, TypeKind


@overload
def is_array(node: Type) -> bool: ...
@overload
def is_array(node: Cursor) -> bool: ...
def is_array(x) -> bool:
    """
    判断输入对象是否为数组类型
    支持Type（类型对象）和Cursor（游标节点）两种输入类型
    :param x: Type对象或Cursor对象
    :return: 是数组类型返回True，否则返回False
    """

    def _is_array_kind(kind: TypeKind) -> bool:
        """内部辅助函数：判断TypeKind是否为数组类型"""
        return kind in (
            TypeKind.CONSTANTARRAY,  # 固定长度数组（如int a[10]）
            TypeKind.INCOMPLETEARRAY,  # 不完整数组（如int a[]）
            TypeKind.VARIABLEARRAY,  # 变长数组（如int a[n]）
        )

    # 处理Type类型输入
    if isinstance(x, Type):
        return _is_array_kind(x.get_canonical().kind)
    # 处理Cursor类型输入
    elif isinstance(x, Cursor):
        x_type: Type = x.type
        if x_type:
            return _is_array_kind(x_type.get_canonical().kind)
    # 非支持类型返回False
    return False


@overload
def type_name(x: Type): ...
@overload
def type_name(x: Cursor): ...
def type_name(x) -> str | None:
    """
    获取对象的完整类型名称（含修饰符）
    支持Type（类型对象）和Cursor（游标节点）两种输入类型
    :param x: Type对象或Cursor对象
    :return: 类型名称字符串（去除首尾空格），无类型信息返回None
    """
    if isinstance(x, Type):
        return x.spelling.strip()
    elif isinstance(x, Cursor):
        if x.type:
            return x.type.spelling.strip()
    else:
        return None


@overload
def pure_type_name(x: Type): ...
@overload
def pure_type_name(x: Cursor): ...
def pure_type_name(x) -> str | None:
    """
    获取对象的纯类型名称（去除数组、指针、const/volatile等修饰）
    支持Type（类型对象）和Cursor（游标节点）两种输入类型
    :param x: Type对象或Cursor对象
    :return: 纯类型名称字符串，无类型信息返回None
    """

    def get_pure_name(t: Type) -> str:
        """内部辅助函数：递归解析纯类型名称"""
        # 去除const/volatile/typedef等修饰，获取规范类型
        t_type: Type = t.get_canonical()
        while True:
            # 如果是数组类型，取数组元素类型继续解析
            if is_array(t_type):
                t_type = t_type.element_type
            # 如果是指针类型，取指针指向类型继续解析
            elif is_pointer(t_type):
                t_type = t_type.get_pointee()
            # 非数组/指针类型，终止解析
            else:
                break
        return t_type.spelling.strip()

    # 处理不同输入类型
    if isinstance(x, Type):
        return get_pure_name(x)
    elif isinstance(x, Cursor):
        if x.type:
            return get_pure_name(x.type)
    else:
        return None


@overload
def is_pointer(node: Type) -> bool: ...
@overload
def is_pointer(node: Cursor) -> bool: ...
def is_pointer(x) -> bool:
    """
    判断输入对象是否为指针类型
    支持Type（类型对象）和Cursor（游标节点）两种输入类型
    :param x: Type对象或Cursor对象
    :return: 是指针类型返回True，否则返回False
    """

    def _is_pointer_kind(kind: TypeKind) -> bool:
        """内部辅助函数：判断TypeKind是否为指针类型"""
        return kind == TypeKind.POINTER

    # 处理Type类型输入
    if isinstance(x, Type):
        return _is_pointer_kind(x.get_canonical().kind)
    # 处理Cursor类型输入
    elif isinstance(x, Cursor):
        x_type: Type = x.type
        if x_type:
            return _is_pointer_kind(x_type.get_canonical().kind)
    # 非支持类型返回False
    return False


@overload
def is_static(x: Type) -> bool: ...
@overload
def is_static(x: Cursor) -> bool: ...
def is_static(x) -> bool:
    """
    判断输入对象是否为static修饰的元素
    仅支持Cursor（游标节点）类型有效判断，Type类型返回False
    :param x: Type对象或Cursor对象
    :return: 是static修饰返回True，否则返回False
    """
    if isinstance(x, Cursor):
        # 检查变量/函数的存储类型是否为static（C语言static）
        if x.storage_class == StorageClass.STATIC:
            return True
        # 检查是否为C++静态方法（兼容C++场景）
        if x.is_static_method():
            return True
    return False


def node_name(x: Cursor | Type) -> str | None:
    """
    获取Cursor或Type对象的拼写名称（标识符）
    :param x: Cursor（游标节点）或Type（类型对象）
    :return: 名称字符串（去除首尾空格），无名称返回None
    """
    return x.spelling.strip()
