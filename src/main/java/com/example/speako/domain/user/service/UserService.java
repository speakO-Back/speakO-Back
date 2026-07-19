package com.example.speako.domain.user.service;

import com.example.speako.domain.user.dto.UserRequestDTO;
import com.example.speako.domain.user.dto.UserResponseDTO;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import com.example.speako.global.config.JwtTokenProvider;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class UserService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;

    @Transactional
    public User signUp(UserRequestDTO.SignUpDTO request) {
        // 1. 비밀번호 일치 검증
        if (!request.getPassword().equals(request.getPasswordCheck())) {
            throw new IllegalArgumentException("입력하신 비밀번호와 비밀번호 확인이 일치하지 않습니다.");
        }

        // 2. 이메일 중복 검증
        userRepository.findByEmail(request.getEmail())
                .ifPresent(user -> {
                    throw new IllegalArgumentException("이미 사용 중인 이메일입니다.");
                });

        // 3. 닉네임 중복 검증 (조건 6)
        if (userRepository.existsByName(request.getName())) {
            throw new IllegalArgumentException("이미 사용 중인 닉네임입니다.");
        }

        // 4. 비밀번호 BCrypt 암호화
        String encodedPassword = passwordEncoder.encode(request.getPassword());

        // 5. 유저 엔티티 생성 및 저장
        User newUser = User.builder()
                .email(request.getEmail())
                .password(encodedPassword)
                .name(request.getName())
                .build();

        return userRepository.save(newUser);
    }

        @Transactional
        public UserResponseDTO.LoginResultDTO login(UserRequestDTO.LoginDTO request) {
            // 1. 이메일로 유저 조회
            User user = userRepository.findByEmail(request.getEmail())
                    .orElseThrow(() -> new IllegalArgumentException("가입되지 않은 이메일입니다."));

            // 2. 비밀번호 검증
            if (!passwordEncoder.matches(request.getPassword(), user.getPassword())) {
                throw new IllegalArgumentException("비밀번호가 일치하지 않습니다.");
            }

            // 3. 인증 성공 시 JWT 토큰 생성
            String accessToken = jwtTokenProvider.createAccessToken(user.getUserId(), user.getEmail());

            return UserResponseDTO.LoginResultDTO.builder()
                    .userId(user.getUserId())
                    .email(user.getEmail())
                    .accessToken(accessToken)
                    .build();
        }
    }
