package com.example.speako.common;
import lombok.Getter;

@Getter
public class ApiResponse<T> {
    private final boolean isSuccess;
    private final String code;
    private final String message;
    private final T result;

    private ApiResponse(boolean isSuccess, String code, String message, T result) {
        this.isSuccess = isSuccess;
        this.code = code;
        this.message = message;
        this.result = result;
    }

    public static <T> ApiResponse<T> onSuccess(T result) {
        return new ApiResponse<>(true, "COMMON200", "요청에 성공하였습니다.", result);
    }

    public static <T> ApiResponse<T> onFailure(String code, String message) {
        return new ApiResponse<>(false, code, message, null);
    }
}