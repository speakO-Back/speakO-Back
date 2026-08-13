package com.example.speako.domain.user.controller;

import com.example.speako.domain.user.dto.UserRequestDTO;
import com.example.speako.domain.user.dto.UserResponseDTO;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.service.UserService;
import com.example.speako.global.common.ApiResponse;
import com.example.speako.global.config.JwtTokenProvider;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/auth")
public class UserController {

    private final UserService userService;
    private final JwtTokenProvider jwtTokenProvider;
    @PostMapping("/signup")
    public ResponseEntity<ApiResponse<UserResponseDTO.SignUpResultDTO>> signUp(
            @RequestBody @Valid UserRequestDTO.SignUpDTO request
    ) {
        User user = userService.signUp(request);

        UserResponseDTO.SignUpResultDTO result = UserResponseDTO.SignUpResultDTO.builder()
                .userId(user.getUserId())
                .email(user.getEmail())
                .name(user.getName())
                .createdAt(user.getCreatedAt())
                .build();

        return ResponseEntity.ok(ApiResponse.onSuccess(result));
    }

    @PostMapping("/login")
    public ResponseEntity<ApiResponse<UserResponseDTO.LoginResultDTO>> login(
            @RequestBody @Valid UserRequestDTO.LoginDTO request
    ) {
        UserResponseDTO.LoginResultDTO result = userService.login(request);
        return ResponseEntity.ok(ApiResponse.onSuccess(result));
    }

    // 이름 변경 (토큰에서 userId 추출)
    @PatchMapping("/name")
    public ResponseEntity<ApiResponse<Void>> updateName(
            @RequestHeader("Authorization") String tokenHeader,
            @RequestBody @Valid UserRequestDTO.UpdateNameDTO request
    ) {
        Long userId = jwtTokenProvider.getUserId(tokenHeader.substring(7));
        userService.updateName(userId, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }

    // 이메일 변경 (토큰에서 userId 추출)
    @PatchMapping("/email")
    public ResponseEntity<ApiResponse<Void>> updateEmail(
            @RequestHeader("Authorization") String tokenHeader,
            @RequestBody @Valid UserRequestDTO.UpdateEmailDTO request
    ) {
        Long userId = jwtTokenProvider.getUserId(tokenHeader.substring(7));
        userService.updateEmail(userId, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }

    // 비밀번호 변경 (토큰에서 userId 추출)
    @PatchMapping("/password")
    public ResponseEntity<ApiResponse<Void>> updatePassword(
            @RequestHeader("Authorization") String tokenHeader,
            @RequestBody @Valid UserRequestDTO.UpdatePasswordDTO request
    ) {
        Long userId = jwtTokenProvider.getUserId(tokenHeader.substring(7));
        userService.updatePassword(userId, request);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }

    // 회원 탈퇴 (토큰에서 userId 추출하도록 서비스와 맞춤)
    @DeleteMapping("/withdraw")
    public ResponseEntity<ApiResponse<Void>> withdraw(
            @RequestHeader("Authorization") String tokenHeader,
            @RequestBody @Valid UserRequestDTO.WithdrawDTO request
    ) {
        Long userId = jwtTokenProvider.getUserId(tokenHeader.substring(7));
        userService.withdraw(userId, request.getPassword());
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }

    // 로그아웃 (토큰에서 userId 추출하도록 서비스와 맞춤)
    @PostMapping("/logout")
    public ResponseEntity<ApiResponse<Void>> logout(
            @RequestHeader("Authorization") String tokenHeader
    ) {
        Long userId = jwtTokenProvider.getUserId(tokenHeader.substring(7));
        userService.logout(userId);
        return ResponseEntity.ok(ApiResponse.onSuccess(null));
    }
}
